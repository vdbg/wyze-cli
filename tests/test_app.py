"""Tests for app.py.

Run from the repository root with either:

    python3 -m unittest discover -s tests
    python3 tests/test_app.py

app.py imports wyze_sdk, which is not needed here and is often not installed
outside the docker image, so a minimal stand-in is registered in sys.modules
before app is imported. No test reaches the network or a real Wyze account:
the api is driven by the "outcomes" list of the scriptable client below.
"""

import json
import logging
import os
import re
import sys
import tempfile
import types
import subprocess
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class Product:

    def __init__(self, model):
        self.model = model


class Camera:

    """Stand-in for wyze_sdk.models.devices.Camera."""

    def __init__(self, nickname, mac, model="WYZEC1"):
        self.nickname = nickname
        self.mac = mac
        self.product = Product(model)


class State:

    """Scripted behaviour of the fake client, reset before every test."""

    def __init__(self):
        self.outcomes = []      # one entry per run_action call: "ok" or an Exception
        self.calls = []         # (mac, action_key) of every action actually sent
        self.refreshes = 0
        self.logins = 0
        self.cameras = []
        self.devices = []
        self.refresh_reply = None
        self.login_reply = None
        self.list_error = False


state = State()


class ApiService:

    """Stand-in for wyze_sdk.service.ApiServiceClient."""

    def run_action(self, mac=None, provider_key=None, action_key=None):
        state.calls.append((mac, action_key))
        outcome = state.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Cameras:

    def list(self):
        if state.list_error:
            raise Exception("401 Client Error: Unauthorized for url: camera/get_all")
        return list(state.cameras)


class Client:

    """Enough of wyze_sdk.Client for app.py to run against."""

    def __init__(self, token=None, refresh_token=None, **kwargs):
        # Named with an underscore, as in the real client: a plain
        # "self.refresh_token" attribute would shadow the method below.
        self._token = token
        self._refresh_token = refresh_token
        self.cameras = Cameras()

    def devices_list(self):
        return list(state.devices)

    def _api_client(self):
        return ApiService()

    def login(self, **kwargs):
        state.logins += 1
        if state.login_reply is None:
            raise RuntimeError("Invalid Login Credentials")
        return state.login_reply

    def refresh_token(self):
        state.refreshes += 1
        if state.refresh_reply is None:
            # what happens when the api answer carries no "data" key
            raise KeyError("'data'")
        return state.refresh_reply


def install_fake_sdk():

    """Register the stand-in so app.py imports without the real wyze_sdk."""

    sdk = types.ModuleType("wyze_sdk")
    sdk.Client = Client
    sdk.models = types.ModuleType("wyze_sdk.models")
    devices = types.ModuleType("wyze_sdk.models.devices")
    devices.Camera = Camera
    sdk.models.devices = devices
    sys.modules["wyze_sdk"] = sdk
    sys.modules["wyze_sdk.models"] = sdk.models
    sys.modules["wyze_sdk.models.devices"] = devices


install_fake_sdk()

import app  # noqa: E402

logging.getLogger().setLevel(logging.CRITICAL)

TOKEN_REJECTED = "TOKEN_EXPIRED (2001): AccessTokenError: access token is expired"
CAMERA_REFUSED = "restart is not supported on this device"
CONNECTION_REFUSED = "HTTPConnectionPool(host='api.wyzecam.com'): Read timed out"


def make_camera(name, mac, model="WYZEC1"):

    return Camera(nickname=name, mac=mac, model=model)


def run_main(*argv):

    """Runs app.main() as if it had been started with the given arguments."""

    with mock.patch.object(sys, "argv", ["app.py", "--wyze-email", "a@b.c",
                                         "--wyze-password", "p", "--wyze-key-id", "k",
                                         "--wyze-api-key", "a", *argv]):
        return app.main()


def make_args(token_file):

    """The Namespace main() would have parsed, so a test cannot forget a field
    that login() needs."""

    with mock.patch.object(sys, "argv", ["app.py", "--action", "power_on",
                                         "--token-file", token_file,
                                         "--wyze-email", "a@b.c", "--wyze-password", "p",
                                         "--wyze-key-id", "k", "--wyze-api-key", "a"]):
        return app.get_args()


class CaseWithTokenFile(unittest.TestCase):

    """A throwaway directory per test, so nothing is written next to app.py."""

    def setUp(self):
        state.__init__()
        self.tmp = tempfile.TemporaryDirectory()
        self.token_file = os.path.join(self.tmp.name, "wyze_token.json")
        self.addCleanup(self.tmp.cleanup)


class ToListTests(unittest.TestCase):

    def test_splits_and_strips(self):
        self.assertEqual(app.to_list(" a , b ,, c "), ["a", "b", "c"])

    def test_empty(self):
        self.assertEqual(app.to_list(""), [])


class CompileRegexTests(unittest.TestCase):

    def test_empty_is_no_filter(self):
        self.assertIsNone(app.compile_regex("regex-include", ""))

    def test_malformed_raises_a_usable_message(self):
        with self.assertRaises(ValueError) as ctx:
            app.compile_regex("regex-include", "[unclosed")
        self.assertIn("--regex-include", str(ctx.exception))

    def test_case_insensitive_flag(self):
        self.assertTrue(app.compile_regex("regex-include", "pan", True).search("PAN-TILT"))
        self.assertIsNone(app.compile_regex("regex-include", "pan", False).search("PAN-TILT"))


class FilterCamerasTests(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.cameras = [
            make_camera("Driveway", "A1:B2:C3:D4:E5:01"),
            make_camera("Garage pan", "a1:b2:c3:d4:e5:02"),
            make_camera("Patio", "A1:B2:C3:D4:E5:03"),
        ]

    def names(self, cameras=None, **kwargs):
        return [c.nickname for c in app.filter_cameras(cameras or self.cameras, **kwargs)]

    def test_no_filter_keeps_everything(self):
        self.assertEqual(self.names(include=[], exclude=[],
                                     regex_include=None, regex_exclude=None),
                         ["Driveway", "Garage pan", "Patio"])

    def test_include_by_name(self):
        self.assertEqual(self.names(include=["Driveway"], exclude=[],
                                     regex_include=None, regex_exclude=None), ["Driveway"])

    def test_exclude_wins_when_a_camera_is_named_twice(self):
        self.assertEqual(self.names(include=["Driveway"], exclude=["Driveway"],
                                     regex_include=None, regex_exclude=None), [])

    def test_macs_match_whatever_their_case(self):
        self.assertEqual(self.names(include=["a1:b2:c3:d4:e5:01"], exclude=[],
                                     regex_include=None, regex_exclude=None), ["Driveway"])
        self.assertEqual(self.names(include=[], exclude=["A1:B2:C3:D4:E5:02"],
                                     regex_include=None, regex_exclude=None),
                         ["Driveway", "Patio"])

    def test_names_need_case_insensitive_to_match_differently(self):
        kwargs = dict(include=["driveway"], exclude=[], regex_include=None, regex_exclude=None)
        self.assertEqual(self.names(**kwargs), [])
        self.assertEqual(self.names(case_insensitive=True, **kwargs), ["Driveway"])

    def test_regex_exclude(self):
        self.assertEqual(self.names(include=[], exclude=[], regex_include=None,
                                     regex_exclude=re.compile("pan$")),
                         ["Driveway", "Patio"])

    def test_a_device_without_name_or_mac_survives(self):
        cameras = [Camera(nickname=None, mac=None)]
        self.assertEqual(len(self.names(cameras, include=[], exclude=[],
                                         regex_include=None, regex_exclude=None)), 1)

class TokenFileTests(CaseWithTokenFile):

    def test_roundtrip_is_private_and_leaves_no_temp_file(self):
        app.save_token(self.token_file, "ACCESS", "REFRESH")

        self.assertEqual(oct(os.stat(self.token_file).st_mode & 0o777), "0o600",
                         "a token file must not be world readable")
        self.assertEqual(os.listdir(self.tmp.name), ["wyze_token.json"],
                         "the temporary file must not survive the write")
        self.assertEqual(app.load_token(self.token_file),
                         {"access_token": "ACCESS", "refresh_token": "REFRESH"})

    def test_replaces_an_existing_token_file(self):
        with open(self.token_file, "w") as f:
            f.write("stale")
        app.save_token(self.token_file, "NEW", "R")
        self.assertEqual(app.load_token(self.token_file)["access_token"], "NEW")

    def test_garbage_is_warned_about_but_not_fatal(self):
        with open(self.token_file, "w") as f:
            f.write("this is not json")
        with self.assertLogs(level="WARNING") as logs:
            self.assertIsNone(app.load_token(self.token_file))
        self.assertIn("malformed", logs.output[0])

    def test_missing_file_is_not_a_warning(self):
        with self.assertLogs(level="DEBUG") as logs:
            self.assertIsNone(app.load_token(os.path.join(self.tmp.name, "nothing.json")))
        self.assertNotIn("WARNING", logs.output[0])

    def test_an_unwritable_token_file_does_not_stop_the_run(self):
        state.cameras = [make_camera("Driveway", "A1:B2:C3:D4:E5:01")]
        state.login_reply = {"access_token": "FRESH", "refresh_token": "R"}
        state.outcomes = ["ok"]
        bad = os.path.join(self.tmp.name, "nope", "wyze_token.json")

        self.assertEqual(run_main("--token-file", bad, "--action", "power_on"), 0)
        self.assertEqual(len(state.calls), 1, "the camera should still have been handled")


class TokenErrorTests(unittest.TestCase):

    def test_an_expired_token_is_a_token_error(self):
        self.assertTrue(app.is_token_error(Exception(TOKEN_REJECTED)))
        self.assertTrue(app.is_token_error(Exception("401 Client Error: Unauthorized")))
        self.assertTrue(app.is_token_error(Exception("403 Client Error: Forbidden")))

    def test_camera_problems_are_not_token_problems(self):
        for message in (CAMERA_REFUSED, CONNECTION_REFUSED):
            self.assertFalse(app.is_token_error(Exception(message)), message)


class PerformActionTests(CaseWithTokenFile):

    def setUp(self):
        super().setUp()
        self.cameras = [make_camera(f"Cam {i}", f"AA:BB:CC:DD:EE:0{i}") for i in range(1, 4)]
        self.client = Client(token="OLD")

    def test_everything_succeeds(self):
        state.outcomes = ["ok"] * 3
        self.assertEqual(app.perform_action(self.client, self.cameras, "power_on"), 0)
        self.assertEqual(len(state.calls), 3)

    def test_a_refused_camera_does_not_buy_a_relogin(self):
        state.outcomes = [Exception(CAMERA_REFUSED)] * 3

        self.assertEqual(app.perform_action(self.client, self.cameras, "restart"), 3)
        self.assertEqual(state.refreshes, 0)
        self.assertEqual(state.logins, 0)
        self.assertEqual(len(state.calls), 3)

    def test_an_expired_token_renews_once_and_retries_that_camera(self):
        app.save_token(self.token_file, "OLD", "R")
        state.refresh_reply = {"data": {"access_token": "NEW", "refresh_token": "R"}}
        state.outcomes = [Exception(TOKEN_REJECTED), "ok", "ok", "ok"]
        args = make_args(self.token_file)

        # fresh_client is wired to the real helper, so the renewal goes through
        # refresh_token() instead of being handed a client for free.
        self.assertEqual(app.perform_action(self.client, self.cameras, "motion_alarm_on",
                                            lambda: app.get_fresh_client(args)), 0)
        self.assertEqual(state.refreshes, 1)
        self.assertEqual(state.logins, 0)
        self.assertEqual(len(state.calls), 4,
                         "only the camera that failed should be sent twice")

    def test_a_dead_token_stops_the_run_and_blames_the_skipped_cameras(self):
        state.outcomes = [Exception(TOKEN_REJECTED)] * 3

        with self.assertLogs(level="WARNING") as logs:
            failed = app.perform_action(self.client, self.cameras, "power_off")

        self.assertEqual(failed, 3, "a skipped camera must not look like a success")
        self.assertEqual(len(state.calls), 1, "the run should stop after the first camera")
        self.assertEqual(state.logins, 0, "nothing to log in with, so no login attempted")
        self.assertIn("2 camera(s) were not attempted", "\n".join(logs.output))

    def test_a_dead_refresh_falls_back_to_a_password_login(self):
        # nothing cached to rotate, so the renewal has to end in a login
        state.login_reply = {"access_token": "FRESH", "refresh_token": "R"}
        state.outcomes = [Exception(TOKEN_REJECTED), "ok", "ok", "ok"]
        args = make_args(self.token_file)

        self.assertEqual(app.perform_action(self.client, self.cameras, "power_on",
                                            lambda: app.get_fresh_client(args)), 0)
        self.assertEqual(state.logins, 1)
        self.assertEqual(len(state.calls), 4)


class MainTests(CaseWithTokenFile):

    """What a whole run returns. The numbers a shell actually reports are pinned
    by CliExitCodeTests below, which runs app.py as a real process."""

    def main(self, *args, cameras=1, list_error=False):
        state.cameras = [make_camera(f"Cam {i}", f"AA:BB:CC:DD:FF:0{i}") for i in range(1, cameras + 1)]
        state.list_error = list_error
        state.login_reply = {"access_token": "FRESH", "refresh_token": "R"}
        return run_main("--token-file", self.token_file, *args)

    def test_a_clean_run_returns_zero(self):
        state.outcomes = ["ok"] * 3
        self.assertEqual(self.main("--action", "power_on", cameras=3), 0)

    def test_the_return_value_is_the_number_of_failures(self):
        state.outcomes = ["ok", Exception(CAMERA_REFUSED), Exception(CAMERA_REFUSED)]
        self.assertEqual(self.main("--action", "restart", cameras=3), 2)

    def test_the_count_is_capped_below_the_reserved_255(self):
        state.outcomes = [Exception(CAMERA_REFUSED)] * 300
        self.assertEqual(self.main("--action", "restart", cameras=300), app.MAX_FAILED_EXIT_CODE)

    def test_a_cached_token_is_used_instead_of_logging_in(self):
        app.save_token(self.token_file, "CACHED", "R")
        state.outcomes = ["ok"]
        self.assertEqual(self.main("--action", "power_on"), 0)
        self.assertEqual(state.logins, 0)
        self.assertEqual(app.load_token(self.token_file)["access_token"], "CACHED")

    def test_a_listing_rejected_by_a_stale_token_retries_once(self):
        app.save_token(self.token_file, "STALE", "R")
        with self.assertRaises(Exception):
            self.main("--action", "power_on", list_error=True)
        self.assertEqual(state.logins, 1, "the retry should have bought exactly one login")


FAKE_SDK = {

    # A throwaway wyze_sdk package, written to a temp directory and put on
    # PYTHONPATH so app.py can be run without a Wyze account. The FAKE_*
    # variables below script what its answers look like.

    "wyze_sdk/__init__.py": """
import os

from wyze_sdk.models.devices import Camera


class _Api:

    def run_action(self, mac=None, provider_key=None, action_key=None):
        mode = os.environ.get("FAKE_ACTION", "ok")
        if mode == "refused":
            raise Exception("restart is not supported on this device")
        if mode == "token":
            raise Exception("TOKEN_EXPIRED (2001): AccessTokenError: token is expired")
        return {}


class _Cameras:

    def list(self):
        count = int(os.environ.get("FAKE_CAMERAS", "3"))
        return [Camera("Cam %d" % i, "AA:BB:CC:DD:EE:%02d" % i) for i in range(1, count + 1)]


class Client:

    def __init__(self, token=None, refresh_token=None, **kwargs):
        self.cameras = _Cameras()

    def devices_list(self):
        return [Camera(), Camera("Router", "AA:BB:CC:00:00:01", "WP2")]

    def _api_client(self):
        return _Api()

    def login(self, **kwargs):
        if os.environ.get("FAKE_LOGIN") == "fail":
            raise Exception("Invalid Login Credentials")
        return {"access_token": "FRESH", "refresh_token": "R"}

    def refresh_token(self):
        if os.environ.get("FAKE_REFRESH") == "fail":
            raise KeyError("'data'")
        return {"data": {"access_token": "NEW", "refresh_token": "R"}}
""",

    "wyze_sdk/models/__init__.py": "",

    "wyze_sdk/models/devices.py": """
class Product:

    def __init__(self, model=None):
        self.model = model


class Camera:

    def __init__(self, nickname=None, mac=None, model="WYZEC1"):
        self.nickname = nickname
        self.mac = mac
        self.product = Product(model)
""",
}


class CliExitCodeTests(CaseWithTokenFile):

    """The exit codes a cron job sees. They are decided by the "if __name__"
    block of app.py, which importing app never reaches, so app.py is run here as
    a real process against the fake package above."""

    def run_app(self, *args, cameras=3, action="ok", login="ok", refresh="ok"):
        stub = os.path.join(self.tmp.name, "stub")
        for name, content in FAKE_SDK.items():
            target = os.path.join(stub, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as f:
                f.write(content)

        env = dict(os.environ, PYTHONPATH=stub, FAKE_ACTION=action, FAKE_LOGIN=login,
                   FAKE_REFRESH=refresh, FAKE_CAMERAS=str(cameras))
        return subprocess.run([sys.executable, os.path.join(ROOT, "app.py"),
                               "--token-file", self.token_file,
                               "--wyze-email", "a@b.c", "--wyze-password", "p",
                               "--wyze-key-id", "k", "--wyze-api-key", "a", *args],
                              capture_output=True, text=True, env=env)

    def exit_code(self, code, process):
        self.assertEqual(process.returncode, code, msg=process.stdout + process.stderr)
        return process

    def test_a_clean_run_exits_zero(self):
        self.exit_code(0, self.run_app("--action", "power_on"))

    def test_two_refused_cameras_exit_two(self):
        self.exit_code(2, self.run_app("--action", "restart", cameras=2, action="refused"))

    def test_many_failures_stop_at_254(self):
        self.exit_code(254, self.run_app("--action", "restart", cameras=300, action="refused"))

    def test_a_dead_token_stops_early_and_reports_the_skipped_cameras(self):
        # A cached token pair, so the run starts from the token rather than from
        # a login, and both ways of getting a new one are broken.
        app.save_token(self.token_file, "STALE", "REFRESHABLE")
        process = self.exit_code(3, self.run_app("--action", "power_off", action="token",
                                                  refresh="fail", login="fail"))
        self.assertIn("not attempted", process.stderr)

    def test_the_listing_commands_exit_zero(self):
        self.exit_code(0, self.run_app("--action", "list"))
        self.exit_code(0, self.run_app("--action", "list_all"))

    def test_a_fatal_error_is_not_a_failure_count(self):
        process = self.exit_code(app.FATAL_EXIT_CODE,
                                 self.run_app("--action", "power_on", login="fail"))
        self.assertIn("Invalid Login Credentials", process.stderr)


if __name__ == "__main__":
    unittest.main()
