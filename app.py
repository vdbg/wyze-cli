import json
import logging
import os
import re
import sys

from argparse import ArgumentParser, RawTextHelpFormatter, Namespace

from typing import Sequence

from wyze_sdk import Client

from wyze_sdk.models.devices import Camera

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


def add_env(parser: ArgumentParser, key: str) -> None:

    val = os.environ.get(key)

    parser.add_argument(
        '--' + key.lower().replace('_', '-'),
        required=val is None,
        default=val,
        metavar="'"+key.lower().replace('_', ' ').capitalize()+"'",
        help=f"Only required if the environment variable {key} is not set."
        )


def get_args() -> Namespace:

    parser = ArgumentParser(description='Bulk manage Wyze cameras. Exit code is the number of failed operations (0 = success; capped at 255)',
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
        choices=['CRITICAL','FATAL','ERROR','WARNING','INFO','DEBUG']
        )

    parser.add_argument(
        '--include',
        metavar='name1,name2',
        default="",
        help='Optional comma delimited list of cameras names of macs to include. If both include and regex_include are not specified, action is performed on all cameras unless excluded.'
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
        help='Optional regex pattern of cameras to include by name or mac. If both include and regex_include are not specified, action is performed on all cameras unless excluded.'
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
        help='Make include/exclude filters case-insensitive.'
    )

    parser.add_argument(
        '--token-file',
        default=os.environ.get('WYZE_TOKEN_FILE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wyze_token.json'),
        help='Where to cache the Wyze access tokens. Defaults to wyze_token.json if the WYZE_TOKEN_FILE env var is not set.'
    )

    add_env(parser, 'WYZE_EMAIL')
    add_env(parser, 'WYZE_PASSWORD')
    add_env(parser, 'WYZE_KEY_ID')
    add_env(parser, 'WYZE_API_KEY')

    return parser.parse_args()


def to_list(s: str) -> list[str]:

    return [item.strip() for item in s.split(',') if item.strip()]


def list_all_devices(client: Client, sort: bool = False) -> int:

    """List all devices including unsupported models."""

    all_devices = client.devices_list()

    if sort:
        all_devices = sorted(all_devices, key=lambda d: d.nickname)

    logging.info("If a camera is showing up here and missing with --list, update the fork https://github.com/vdbg/wyze-sdk/tree/add_camera_models:")

    for device in all_devices:
        logging.info(f"Found device: {device.nickname} with model {device.product.model}")

    return 0


def filter_cameras(cameras: Sequence[Camera], include: list[str], exclude: list[str], regex_include: re.Pattern | None, regex_exclude: re.Pattern | None,
case_insensitive: bool = False) -> list[Camera]:

    """Filter cameras based on include/exclude rules."""

    filtered: list[Camera] = []

    # Create sets for efficient lookup
    include_set = {i.casefold() for i in include} if case_insensitive else set(include)
    exclude_set = {e.casefold() for e in exclude} if case_insensitive else set(exclude)

    logging.info(f"Include: {include}; exclude: {exclude}; regex_include: {regex_include.pattern if regex_include else 'None'}; regex_exclude: {regex_exclude.pattern if regex_exclude else 'None'}; case_insensitive: {case_insensitive}")

    for camera in cameras:

        # Normalize camera identifiers based on case sensitivity
        cam_name = camera.nickname.casefold() if case_insensitive else camera.nickname
        cam_mac = camera.mac.casefold() if case_insensitive else camera.mac

        # Check include filters (OR condition)
        if include or regex_include:

            matches_include = include and (cam_name in include_set or cam_mac in include_set)
            matches_regex_include = regex_include and (regex_include.search(camera.nickname) or regex_include.search(camera.mac))

            if not (matches_include or matches_regex_include):
                logging.debug(f"{camera.nickname} with mac {camera.mac} doesn't match any include filter.")
                continue

        # Check exclude filters (OR condition)
        if exclude and (cam_name in exclude_set or cam_mac in exclude_set):
            logging.debug(f"{camera.nickname} with mac {camera.mac} in exclude list.")
            continue

        if regex_exclude and (regex_exclude.search(camera.nickname) or regex_exclude.search(camera.mac)):
            logging.debug(f"{camera.nickname} with mac {camera.mac} matches regex_exclude pattern.")
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
        raise ValueError(f"Invalid regex pattern for --{name}: {e}")


def load_token(path: str) -> dict | None:
    """Read cached tokens; None when the file is missing or unreadable."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_token(path: str, access_token: str | None, refresh_token: str | None) -> None:
    """Atomically write tokens, mode 0600: they are credentials."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def login_and_save_token(args: Namespace) -> Client:
    """One password login, cache the tokens, return a token client."""
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
    """Refresh the saved access token, cache the new pair, return a token client."""
    saved = load_token(args.token_file)
    if not saved or not saved.get("refresh_token"):
        raise ValueError("No saved Wyze refresh token")
    client = Client(token=saved.get("access_token"), refresh_token=saved["refresh_token"])
    # wyze-sdk nests the rotated tokens under "data" in the refresh response
    # (Client.refresh_token() reads response["data"]["access_token"] /
    # response["data"]["refresh_token"]); the top level has no tokens.
    response = client.refresh_token()
    data = response.get("data") or {}
    access_token = data.get("access_token")
    if not access_token:
        logging.warning("Wyze refresh response had no access token under 'data'; keeping previous token cache")
        return client
    try:
        save_token(args.token_file, access_token, data.get("refresh_token") or saved["refresh_token"])
    except OSError as e:
        logging.warning("Could not cache Wyze token in %s: %s", args.token_file, e)
    return client


def perform_action(client: Client, cameras: list[Camera], action: str) -> int:

    """Perform the specified action on filtered cameras."""

    fail = 0
    success = 0

    for camera in cameras:

        if action == "list":
            logging.info(f"Found device: {camera.nickname} with mac {camera.mac}")
            continue

        try:
            response = client._api_client().run_action(mac=camera.mac, provider_key=camera.product.model, action_key=action)

            if response.status_code != 200:
                logging.error(f"Could not perform {action} on {camera.nickname} with mac {camera.mac}. Code {response.status_code}. Content: {response.data}")
                fail += 1

            else:
                logging.info(f"Successfully performed {action} on {camera.nickname}")
                success += 1

        except Exception as e:
            logging.error(f"Could not perform {action} on {camera.nickname} with mac {camera.mac}: {e}.")
            fail += 1

    if success:
        logging.info(f"Successfully performed {success} {action} operation(s).")

    if fail:
        logging.warning(f"Failed to perform {fail} {action} operation(s).")

    return fail


def dispatch(args: Namespace, client: Client, regex_include: re.Pattern | None, regex_exclude: re.Pattern | None) -> int:
    """Run the requested action. Raises on API failure so the caller can retry."""
    if args.action == "list_all":
        return list_all_devices(client, args.sort)

    cameras = filter_cameras(client.cameras.list(), to_list(args.include), to_list(args.exclude), regex_include, regex_exclude, args.case_insensitive)

    if args.sort:
        cameras = sorted(cameras, key=lambda c: c.nickname)

    return perform_action(client, cameras, args.action)


try:

    args = get_args()

    if args.log_level:
        logging.getLogger().setLevel(args.log_level)

    # Compile and validate regex patterns before any login, so a bad pattern
    # fails fast instead of spending a login and a retry.
    regex_include = compile_regex("regex-include", args.regex_include, args.case_insensitive)
    regex_exclude = compile_regex("regex-exclude", args.regex_exclude, args.case_insensitive)

    client, from_token = get_client(args)

    try:
        ret = dispatch(args, client, regex_include, regex_exclude)
    except Exception as e:
        if not from_token:
            raise
        try:
            ret = dispatch(args, refresh_and_save_token(args), regex_include, regex_exclude)
        except Exception:
            # Saved token rejected (typically expired): one fresh login, one retry.
            logging.warning("Saved Wyze token rejected (%s); logging in once and retrying", e)
            ret = dispatch(args, login_and_save_token(args), regex_include, regex_exclude)

    # Exit codes are 8 bits
    if exit_code > 255:
        exit_code = 255

    sys.exit(exit_code)

except Exception as e:
    logging.critical(e)
    sys.exit(-1)
