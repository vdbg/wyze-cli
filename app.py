import json
import logging
import os
import re
import sys
import tempfile

from argparse import ArgumentParser, RawTextHelpFormatter, Namespace

from typing import Callable, Sequence

from wyze_sdk import Client

from wyze_sdk.models.devices import Camera

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# The app exits with the number of failed operations so that a cron job can
# report them (see README), so 255 is kept for "the app itself failed": bad
# credentials, malformed regex or an api error before any action was attempted.
FATAL_EXIT_CODE = 255
MAX_FAILED_EXIT_CODE = 254

# Errors that mean "Wyze does not accept this access token anymore". The sdk
# reports an expired token as a WyzeApiError (api code 2001 or AccessTokenError)
# and an http 401/403 as a WyzeRequestError, the message being the only signal
# common to both. An unsupported action or an unreachable camera must NOT match:
# it would buy a useless re-login for every camera.
TOKEN_ERROR_PATTERN = re.compile(
    r"access token|AccessTokenError|not logged in|40[13] Client Error", re.IGNORECASE)


def is_token_error(error: Exception) -> bool:

    """True when Wyze rejected the access token the client is carrying."""

    return TOKEN_ERROR_PATTERN.search(str(error)) is not None


def add_env(parser: ArgumentParser, key: str) -> None:

    # An empty variable counts as unset: an empty password only wastes a login.
    val = os.environ.get(key) or None

    parser.add_argument(
        '--' + key.lower().replace('_', '-'),
        required=val is None,
        default=val,
        metavar="'"+key.lower().replace('_', ' ').capitalize()+"'",
        help=f"Only required if the environment variable {key} is not set."
        )


def get_args() -> Namespace:

    parser = ArgumentParser(
        description='Bulk manage Wyze cameras. Exit code is the number of failed operations (0 = success; capped at 254); 255 means the app itself failed (bad credentials, malformed regex, unreachable API).',
        formatter_class=RawTextHelpFormatter)

    parser.add_argument(
        '--action',
        choices=[
            'power_on', 'power_off',
            'motion_alarm_on', 'motion_alarm_off',
            'floodlight_on', 'floodlight_off',
            'restart', 'list', 'list_all'
            ],

        required=True,

        help='''Type of action to perform on selected cameras:
  power_on           - Turn camera on
  power_off          - Turn camera off
  motion_alarm_on    - Turn on motion detection alarm
  motion_alarm_off   - Turn off motion detection alarm
  floodlight_on      - Turn floodlight on
  floodlight_off     - Turn floodlight off
  restart            - Restart camera
  list_all           - List all the devices including unsupported models, ignoring the filters
  list               - Lists supported cameras with the include/exclude filters applied

'''
        )

    parser.add_argument(
        '--log-level',
        type=str.upper,
        metavar='LEVEL',
        choices=['CRITICAL','FATAL','ERROR','WARNING','INFO','DEBUG'],
        help='Optional logging level. Case insensitive: --log-level=debug works too.'
        )

    parser.add_argument(
        '--include',
        metavar='name1,name2',
        default="",
        help='Optional comma delimited list of cameras names or macs to include. If neither include nor regex_include is specified, action is performed on all cameras unless excluded.'
    )

    parser.add_argument(
        '--exclude',
        metavar="name1,name2",
        default="",
        help='Optional comma delimited list of cameras names or macs to exclude.'
    )

    parser.add_argument(
        '--regex-include',
        metavar='pattern',
        default="",
        help='Optional regex pattern of cameras to include by name or mac. If neither include nor regex_include is specified, action is performed on all cameras unless excluded.'
    )

    parser.add_argument(
        '--regex-exclude',
        metavar='pattern',
        default="",
        help='Optional regex pattern of cameras to exclude by name or mac.'
    )

    parser.add_argument(
        '--sort',
        action='store_true',
        help='Sort filtered cameras by name in ascending order.'
    )

    parser.add_argument(
        '--case-insensitive',
        action='store_true',
        help='Make include/exclude filters case insensitive. Macs are always matched case insensitively.'
    )

    parser.add_argument(
        '--token-file',
        default=os.environ.get('WYZE_TOKEN_FILE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wyze_token.json'),
        help='''Where to cache the Wyze access tokens. Defaults to wyze_token.json next to
this script when the WYZE_TOKEN_FILE env var is not set. Without a cached token
the app performs a password login on every run, which Wyze throttles: in docker,
point WYZE_TOKEN_FILE at a mounted volume to keep the token between runs.'''
    )

    add_env(parser, 'WYZE_EMAIL')
    add_env(parser, 'WYZE_PASSWORD')
    add_env(parser, 'WYZE_KEY_ID')
    add_env(parser, 'WYZE_API_KEY')

    return parser.parse_args()


def to_list(s: str) -> list[str]:

    return [item.strip() for item in s.split(',') if item.strip()]


def list_all_devices(client: Client, sort: bool = False) -> int:

    """List all devices including unsupported models.

    Names and models are read defensively: this command exists to help spot
    devices the sdk does not know about, so one odd device must not take the
    whole listing down with it.
    """

    all_devices = client.devices_list()

    if sort:
        all_devices = sorted(all_devices, key=lambda d: getattr(d, "nickname", None) or "")

    logging.info("If a camera is showing up here and missing with --list, update the fork https://github.com/vdbg/wyze-sdk/tree/add_camera_models:")

    for device in all_devices:
        name = getattr(device, "nickname", None) or "unnamed device"
        model = getattr(getattr(device, "product", None), "model", None)
        logging.info("Found device: %s with %s", name,
                     f"model {model}" if model else "no model reported")

    return 0


def filter_cameras(cameras: Sequence[Camera], include: list[str], exclude: list[str], regex_include: re.Pattern | None, regex_exclude: re.Pattern | None,
                   case_insensitive: bool = False) -> list[Camera]:

    """Filter cameras based on include/exclude rules.

    Each filter entry is matched against the camera name and against its mac.
    Macs are hexadecimal identifiers, so they are compared case insensitively
    either way; names only when case_insensitive is set. A camera named in both
    lists ends up excluded.
    """

    filtered: list[Camera] = []

    def normalize(value: str) -> str:
        return value.casefold() if case_insensitive else value

    # Create sets for efficient lookup
    include_names = {normalize(item) for item in include}
    exclude_names = {normalize(item) for item in exclude}
    include_macs = {item.casefold() for item in include}
    exclude_macs = {item.casefold() for item in exclude}

    logging.info("Include: %s; exclude: %s; regex_include: %s; regex_exclude: %s; case_insensitive: %s",
                 include, exclude, regex_include.pattern if regex_include else None,
                 regex_exclude.pattern if regex_exclude else None, case_insensitive)

    for camera in cameras:

        # A device can report no name or no mac: match on empty strings instead
        # of letting the filters blow up on None.
        name = camera.nickname or ""
        mac = camera.mac or ""
        cam_name = normalize(name)
        cam_mac = mac.casefold()

        # Check include filters (OR condition)
        if include or regex_include:

            matches_include = cam_name in include_names or cam_mac in include_macs
            matches_regex_include = regex_include is not None and (regex_include.search(name) or regex_include.search(mac))

            if not (matches_include or matches_regex_include):
                logging.debug("%s with mac %s doesn't match any include filter.", name, mac)
                continue

        # Check exclude filters (OR condition)
        if cam_name in exclude_names or cam_mac in exclude_macs:
            logging.debug("%s with mac %s in exclude list.", name, mac)
            continue

        if regex_exclude is not None and (regex_exclude.search(name) or regex_exclude.search(mac)):
            logging.debug("%s with mac %s matches regex_exclude pattern.", name, mac)
            continue

        filtered.append(camera)

    return filtered


def compile_regex(name: str, value: str, case_insensitive: bool = False) -> re.Pattern | None:

    """Compile a regex pattern and provide a helpful error message if malformed.

    Args:
        name: The parameter name (e.g., "regex-include")
        value: The regex pattern string
        case_insensitive: If True, compile with re.IGNORECASE flag

    Returns:
        Compiled regex pattern, or None if value is empty

    Raises:
        ValueError: If the regex pattern is malformed
    """

    if not value:
        return None

    try:
        flags = re.IGNORECASE if case_insensitive else 0
        return re.compile(value, flags)

    except re.error as e:
        raise ValueError(f"Invalid regex pattern for --{name}: {e}") from e


def load_token(path: str) -> dict | None:
    """Read cached tokens; None when the file is missing or unreadable."""

    try:
        with open(path) as f:
            data = json.load(f)
    except OSError as e:
        logging.debug("No Wyze token cache to read in %s: %s", path, e)
        return None
    except ValueError as e:
        logging.warning("Ignoring malformed Wyze token cache in %s: %s", path, e)
        return None

    return data if isinstance(data, dict) else None


def save_token(path: str, access_token: str | None, refresh_token: str | None) -> None:
    """Write the tokens to a private temporary file, then move it into place.

    mkstemp creates the file with mode 0600, so the credentials are never left
    world readable even briefly, and its random name keeps two containers that
    share one token volume from overwriting each other's half written file.
    """

    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                                   prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as f:
            json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def login_and_save_token(args: Namespace) -> Client:
    """One password login, cache the tokens, return a token client."""

    # A login response carries access_token/refresh_token at the top level,
    # unlike a refresh response that nests them under "data".
    response = Client().login(
        email=args.wyze_email,
        password=args.wyze_password,
        key_id=args.wyze_key_id,
        api_key=args.wyze_api_key)
    access_token = response.get("access_token")
    if not access_token:
        raise ValueError("Wyze login returned no access token")
    try:
        save_token(args.token_file, access_token, response.get("refresh_token"))
    except OSError as e:
        logging.warning("Could not cache Wyze token in %s: %s", args.token_file, e)
    return Client(token=access_token)


def get_client(args: Namespace) -> tuple[Client, bool]:
    """Build a client from the cached token when possible, else log in once.

    Returns (client, from_token). from_token tells the caller a saved token was
    used.
    """

    saved = load_token(args.token_file)
    if saved and saved.get("access_token"):
        logging.info("Using saved Wyze token from %s", args.token_file)
        return Client(token=saved["access_token"], refresh_token=saved.get("refresh_token")), True
    return login_and_save_token(args), False


def refresh_and_save_token(args: Namespace) -> Client:
    """Rotate the cached access token, cache the new pair, return a client.

    Client.refresh_token() installs the rotated pair on the client it returns,
    so this only mirrors it to disk. A refresh that yields no usable pair means
    the cached refresh token is no longer worth anything, so this raises and
    lets the caller fall back to a password login instead of carrying on with a
    token Wyze has already rejected.
    """

    saved = load_token(args.token_file)
    if not saved or not saved.get("refresh_token"):
        raise ValueError("No saved Wyze refresh token")

    client = Client(token=saved.get("access_token"), refresh_token=saved["refresh_token"])

    try:
        # wyze-sdk nests the rotated tokens under "data" in the refresh response
        # (Client.refresh_token() reads response["data"]["access_token"] and
        # response["data"]["refresh_token"] eagerly, hence the except below);
        # the top level has no tokens.
        response = client.refresh_token()
        data = response.get("data") or {}
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("Wyze refresh returned no token pair under 'data'") from e

    access_token = data.get("access_token")
    if not access_token:
        raise ValueError("Wyze refresh response carried no access token")

    try:
        save_token(args.token_file, access_token, data.get("refresh_token") or saved["refresh_token"])
    except OSError as e:
        logging.warning("Could not cache Wyze token in %s: %s", args.token_file, e)

    return client


def get_fresh_client(args: Namespace) -> Client:
    """Return a client with a live token: rotate the cached pair, else log in."""

    try:
        return refresh_and_save_token(args)
    except Exception as e:
        logging.warning("Could not rotate the cached Wyze token (%s); logging in instead", e)
        return login_and_save_token(args)


def perform_action(client: Client, cameras: list[Camera], action: str,
                   fresh_client: Callable[[], Client] | None = None) -> int:

    """Perform the specified action on the filtered cameras and return the
    number of cameras it did not succeed on.

    A rejected access token fails identically on every camera still on the
    list, so the first rejection buys one new token and retries the camera that
    hit it. If no new token can be had, the run stops there instead of spending
    one slow useless call per camera, and the cameras left unattempted are
    counted as failures so the exit code still says what did not work.
    """

    fail = 0
    success = 0
    token_renewed = False
    give_up = False

    for index, camera in enumerate(cameras):

        if action == "list":
            logging.info("Found device: %s with mac %s", camera.nickname, camera.mac)
            continue

        while True:

            try:
                # There is no response code to look at here: the sdk raises on
                # any failure (http error, or api code other than "1"), so
                # getting past this line means Wyze accepted the request.
                client._api_client().run_action(mac=camera.mac, provider_key=camera.product.model, action_key=action)
                logging.info("Successfully performed %s on %s", action, camera.nickname)
                success += 1
                break

            except Exception as e:

                if not is_token_error(e):
                    logging.error("Could not perform %s on %s with mac %s: %s.", action, camera.nickname, camera.mac, e)
                    fail += 1
                    break

                if not token_renewed and fresh_client is not None:
                    token_renewed = True
                    logging.warning("Wyze rejected the access token (%s)", e)
                    try:
                        client = fresh_client()
                        logging.info("Retrying %s with a new access token", camera.nickname)
                        continue
                    except Exception as refresh_error:
                        logging.warning("Could not get a new access token (%s)", refresh_error)

                # Either the token was already renewed once this run or no new
                # token could be had: the rest of the list fails the same way.
                give_up = True
                fail += len(cameras) - index
                logging.warning("Stopped after %s: no usable Wyze token, %s camera(s) were not attempted.",
                                camera.nickname, len(cameras) - index - 1)
                break

        if give_up:
            break

    if success:
        logging.info("Successfully performed %d %s operation(s).", success, action)

    if fail:
        logging.warning("Failed to perform %d %s operation(s).", fail, action)

    return fail


def dispatch(args: Namespace, client: Client, regex_include: re.Pattern | None, regex_exclude: re.Pattern | None) -> int:
    """Run the requested action on the cameras the filters kept."""

    if args.action == "list_all":
        return list_all_devices(client, args.sort)

    cameras = filter_cameras(client.cameras.list(), to_list(args.include), to_list(args.exclude),
                             regex_include, regex_exclude, args.case_insensitive)

    if args.sort:
        cameras = sorted(cameras, key=lambda c: c.nickname or "")

    return perform_action(client, cameras, args.action, lambda: get_fresh_client(args))


def main() -> int:

    args = get_args()

    if args.log_level:
        logging.getLogger().setLevel(args.log_level)

    # Compile and validate regex patterns before any login, so a bad pattern
    # fails fast instead of spending a login and a retry.
    regex_include = compile_regex("regex-include", args.regex_include, args.case_insensitive)
    regex_exclude = compile_regex("regex-exclude", args.regex_exclude, args.case_insensitive)

    client, from_token = get_client(args)

    try:
        failures = dispatch(args, client, regex_include, regex_exclude)

    except Exception as e:
        if not from_token:
            raise
        logging.warning("Run failed with a saved Wyze token (%s); retrying once", e)
        # The cached token is stale. The device listing is what failed, so no
        # camera has been acted on yet and the retry cannot repeat an action.
        failures = dispatch(args, get_fresh_client(args), regex_include, regex_exclude)

    # Keep the exit code inside the 8 bits a shell reports, and keep 255 for
    # "the app itself failed" so that the two cannot be confused.
    return min(failures, MAX_FAILED_EXIT_CODE)


if __name__ == "__main__":

    try:
        sys.exit(main())

    except Exception as e:
        logging.critical("%s", e)
        logging.debug("Traceback:", exc_info=True)
        # 255 stays reserved for "the app itself failed", so it cannot be
        # mistaken for a run where 255 cameras failed.
        sys.exit(FATAL_EXIT_CODE)
