"""Azure Function: proxy launch endpoint for the VM launcher service."""

import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

import azure.functions as func
import requests

AUTH_HEADER_NAME = "x-job-launcher-secret"
DEFAULT_LAUNCHER_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class LauncherConfig:
    launcher_base_url: str
    shared_secret: str
    launcher_timeout_seconds: float


def _load_config() -> LauncherConfig:
    missing: list[str] = []

    launcher_base_url = os.getenv("VM_LAUNCHER_BASE_URL", "").strip()
    if not launcher_base_url:
        missing.append("VM_LAUNCHER_BASE_URL")

    shared_secret = os.getenv("JOB_LAUNCHER_SHARED_SECRET", "").strip()
    if not shared_secret:
        missing.append("JOB_LAUNCHER_SHARED_SECRET")

    if missing:
        raise RuntimeError(
            "Launcher is missing required environment variables: " + ", ".join(missing)
        )

    timeout_raw = os.getenv("VM_LAUNCHER_TIMEOUT_SECONDS", "").strip()
    timeout_seconds = DEFAULT_LAUNCHER_TIMEOUT_SECONDS
    if timeout_raw:
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = DEFAULT_LAUNCHER_TIMEOUT_SECONDS
    if timeout_seconds <= 0:
        timeout_seconds = DEFAULT_LAUNCHER_TIMEOUT_SECONDS

    return LauncherConfig(
        launcher_base_url=launcher_base_url.rstrip("/"),
        shared_secret=shared_secret,
        launcher_timeout_seconds=timeout_seconds,
    )


def _launcher_url(config: LauncherConfig) -> str:
    return f"{config.launcher_base_url}/launch"


def _parse_response_body(response: requests.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        parsed = response.json()
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}

    text = (response.text or "").strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except json.JSONDecodeError:
        return {"raw": text[:1000]}


def _proxy_launch(config: LauncherConfig, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = requests.post(
        _launcher_url(config),
        headers={
            AUTH_HEADER_NAME: config.shared_secret,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=config.launcher_timeout_seconds,
    )
    body = _parse_response_body(response)
    response.raise_for_status()
    return response.status_code, body


def _json_response(payload: dict[str, Any], status_code: int) -> Any:
    return func.HttpResponse(
        body=json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


def _provided_secret(request: Any) -> str:
    header_value = request.headers.get(AUTH_HEADER_NAME)
    if header_value:
        return header_value.strip()

    # Backward-compatible alias while clients migrate.
    fallback = request.headers.get("x-launcher-secret", "")
    return fallback.strip()


def _is_authorized(request: Any, config: LauncherConfig) -> bool:
    provided = _provided_secret(request)
    if not provided:
        return False
    return hmac.compare_digest(provided, config.shared_secret)


def _response_preview(response: requests.Response | None) -> str:
    if response is None:
        return ""
    return (response.text or "").strip()[:500]


def main(req: func.HttpRequest) -> func.HttpResponse:
    """Proxy launch requests to the VM launcher service."""
    try:
        config = _load_config()
    except RuntimeError as exc:
        return _json_response(
            {
                "ok": False,
                "error": "launcher_misconfigured",
                "message": str(exc),
            },
            status_code=500,
        )

    if not _is_authorized(req, config):
        return _json_response(
            {
                "ok": False,
                "error": "unauthorized",
                "message": f"Missing or invalid {AUTH_HEADER_NAME} header.",
            },
            status_code=401,
        )

    try:
        try:
            request_payload = req.get_json()
        except ValueError:
            request_payload = {}

        if request_payload is None:
            request_payload = {}
        if not isinstance(request_payload, dict):
            request_payload = {"value": request_payload}

        proxied_status, proxied_body = _proxy_launch(config, request_payload)
        return _json_response(
            {
                "ok": True,
                "launcherBaseUrl": config.launcher_base_url,
                "launcherStatusCode": proxied_status,
                **proxied_body,
            },
            status_code=202,
        )
    except requests.HTTPError as exc:
        upstream_status = exc.response.status_code if exc.response is not None else 502
        return _json_response(
            {
                "ok": False,
                "error": "vm_launcher_api_error",
                "upstreamStatusCode": upstream_status,
                "message": _response_preview(exc.response) or str(exc),
            },
            status_code=502,
        )
    except requests.RequestException as exc:
        return _json_response(
            {
                "ok": False,
                "error": "vm_launcher_unreachable",
                "message": str(exc),
            },
            status_code=502,
        )
