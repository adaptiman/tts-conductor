import base64
import hashlib
import hmac
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def _install_test_stubs() -> None:
    if "docker" not in sys.modules:
        docker_module = types.ModuleType("docker")

        class _DummyDockerClient:
            pass

        def _from_env():
            return _DummyDockerClient()

        docker_module.from_env = _from_env

        docker_errors = types.ModuleType("docker.errors")

        class APIError(Exception):
            pass

        class DockerException(Exception):
            pass

        class NotFound(Exception):
            pass

        docker_errors.APIError = APIError
        docker_errors.DockerException = DockerException
        docker_errors.NotFound = NotFound
        docker_module.errors = docker_errors

        sys.modules["docker"] = docker_module
        sys.modules["docker.errors"] = docker_errors

    if "dotenv" not in sys.modules:
        dotenv_module = types.ModuleType("dotenv")

        def _dotenv_values(_path):
            return {}

        dotenv_module.dotenv_values = _dotenv_values
        sys.modules["dotenv"] = dotenv_module

    if "fastapi" not in sys.modules:
        fastapi_module = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail: str):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class FastAPI:
            def __init__(self, *args, **kwargs):
                pass

            def add_middleware(self, *args, **kwargs):
                return None

            def get(self, *args, **kwargs):
                def _decorator(func):
                    return func

                return _decorator

            def post(self, *args, **kwargs):
                def _decorator(func):
                    return func

                return _decorator

            def options(self, *args, **kwargs):
                def _decorator(func):
                    return func

                return _decorator

        def Header(default=None):
            return default

        def Query(default=""):
            return default

        class Request:
            pass

        fastapi_module.FastAPI = FastAPI
        fastapi_module.Header = Header
        fastapi_module.HTTPException = HTTPException
        fastapi_module.Query = Query
        fastapi_module.Request = Request
        sys.modules["fastapi"] = fastapi_module

        fastapi_middleware_module = types.ModuleType("fastapi.middleware")
        fastapi_cors_module = types.ModuleType("fastapi.middleware.cors")

        class CORSMiddleware:
            pass

        fastapi_cors_module.CORSMiddleware = CORSMiddleware
        sys.modules["fastapi.middleware"] = fastapi_middleware_module
        sys.modules["fastapi.middleware.cors"] = fastapi_cors_module


_install_test_stubs()

APP_PATH = Path(__file__).resolve().parents[1] / "vm" / "launcher" / "app.py"
SPEC = importlib.util.spec_from_file_location("vm_launcher_app", APP_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module spec from {APP_PATH}")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class _DummyRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _config(**overrides):
    base = APP.LauncherConfig(
        shared_secret="launcher-secret",
        bot_image="tts-conductor:local",
        bot_command="python ip_conductor.py --voice --voice-transport daily --headless",
        bot_container_name="tts-conductor-bot",
        bot_env_file="/tmp/.env",
        bot_network="tts-conductor-bot-net",
        bot_pull_on_start=False,
        bot_memory_limit="",
        bot_nano_cpus=None,
        hook_shared_secret="",
        hook_hmac_secret="",
        webhook_room_name="",
        stop_via_webhook_enabled=False,
        start_on_unrecognized_event=True,
    )
    return base.__class__(**{**base.__dict__, **overrides})


class VmLauncherDailyHookTests(unittest.TestCase):
    def test_resolve_action_start_event(self):
        cfg = _config(stop_via_webhook_enabled=False)
        payload = {"event": "participant.joined"}
        action, source = APP._resolve_daily_hook_action("", payload, cfg)
        self.assertEqual(action, "start")
        self.assertEqual(source, "participant.joined")

    def test_resolve_action_stop_disabled(self):
        cfg = _config(stop_via_webhook_enabled=False)
        payload = {"event": "meeting.ended"}
        action, source = APP._resolve_daily_hook_action("", payload, cfg)
        self.assertEqual(action, "")
        self.assertEqual(source, "meeting.ended")

    def test_resolve_action_stop_enabled(self):
        cfg = _config(stop_via_webhook_enabled=True)
        payload = {"event": "meeting.ended"}
        action, source = APP._resolve_daily_hook_action("", payload, cfg)
        self.assertEqual(action, "stop")
        self.assertEqual(source, "meeting.ended")

    def test_authorize_with_shared_secret_query_param(self):
        cfg = _config(hook_shared_secret="hook-secret")
        request = _DummyRequest(headers={})
        authorized = APP._is_daily_hook_authorized(
            request=request,
            payload={},
            config=cfg,
            raw_body=b"{}",
            query_secret="hook-secret",
        )
        self.assertTrue(authorized)

    def test_authorize_with_hmac_signature(self):
        raw_body = b'{"event":"meeting.started"}'
        hmac_secret = "hmac-test-secret"
        digest = hmac.new(
            hmac_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).digest()
        sig = base64.b64encode(digest).decode("ascii")
        request = _DummyRequest(headers={"x-daily-signature": f"sha256={sig}"})
        cfg = _config(hook_hmac_secret=hmac_secret, hook_shared_secret="ignored")

        authorized = APP._is_daily_hook_authorized(
            request=request,
            payload={},
            config=cfg,
            raw_body=raw_body,
            query_secret="",
        )
        self.assertTrue(authorized)

    def test_authorize_with_webhook_signature_and_timestamp(self):
        raw_body = b'{"event":"meeting.started"}'
        hmac_secret = "hmac-test-secret"
        timestamp = "1711641600"
        signing_input = f"{timestamp}.".encode() + raw_body
        digest = hmac.new(
            hmac_secret.encode(),
            signing_input,
            hashlib.sha256,
        ).digest()
        sig = base64.b64encode(digest).decode("ascii")
        request = _DummyRequest(
            headers={
                "x-webhook-signature": sig,
                "x-webhook-timestamp": timestamp,
            }
        )
        cfg = _config(hook_hmac_secret=hmac_secret)

        authorized = APP._is_daily_hook_authorized(
            request=request,
            payload={},
            config=cfg,
            raw_body=raw_body,
            query_secret="",
        )
        self.assertTrue(authorized)

    def test_hmac_mode_takes_precedence_over_shared_secret(self):
        raw_body = b'{"event":"meeting.started"}'
        request = _DummyRequest(headers={})
        cfg = _config(
            hook_hmac_secret="hmac-test-secret",
            hook_shared_secret="shared-fallback",
        )

        authorized = APP._is_daily_hook_authorized(
            request=request,
            payload={},
            config=cfg,
            raw_body=raw_body,
            query_secret="shared-fallback",
        )
        self.assertFalse(authorized)

    def test_authorize_with_invalid_hmac_signature_fails(self):
        raw_body = b'{"event":"meeting.started"}'
        request = _DummyRequest(headers={"x-daily-signature": "sha256=invalid"})
        cfg = _config(hook_hmac_secret="hmac-test-secret")

        authorized = APP._is_daily_hook_authorized(
            request=request,
            payload={},
            config=cfg,
            raw_body=raw_body,
            query_secret="",
        )
        self.assertFalse(authorized)

    def test_rooms_match_by_tail_segment(self):
        self.assertTrue(APP._rooms_match("my-room", "https://foo.daily.co/my-room"))
        self.assertFalse(APP._rooms_match("my-room", "https://foo.daily.co/other-room"))


if __name__ == "__main__":
    unittest.main()
