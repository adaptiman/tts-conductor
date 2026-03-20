"""Azure Function: launcher status proxy endpoint."""

import json
import os
from dataclasses import dataclass
from typing import Any

import azure.functions as func
import requests

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


def _status_url(config: LauncherConfig) -> str:
    return f"{config.launcher_base_url}/status"


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


def _proxy_status(config: LauncherConfig) -> tuple[int, dict[str, Any]]:
    response = requests.get(
        _status_url(config),
        headers={"x-job-launcher-secret": config.shared_secret},
        timeout=config.launcher_timeout_seconds,
    )
    body = _parse_response_body(response)
    response.raise_for_status()
    return response.status_code, body


def _response_preview(response: requests.Response | None) -> str:
    if response is None:
        return ""
    return (response.text or "").strip()[:500]


def _json_response(payload: dict[str, Any], status_code: int) -> Any:
    return func.HttpResponse(
        body=json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


def main(_req: func.HttpRequest) -> func.HttpResponse:
    """Quick diagnostics endpoint for launcher health and active bot state."""
    try:
        config = _load_config()
        launcher_status_code, launcher_payload = _proxy_status(config)

        return _json_response(
            {
                "ok": True,
                "launcherBaseUrl": config.launcher_base_url,
                "launcherStatusCode": launcher_status_code,
                "launcher": launcher_payload,
            },
            status_code=200,
        )
    except RuntimeError as exc:
        return _json_response(
            {
                "ok": False,
                "error": "launcher_misconfigured",
                "message": str(exc),
            },
            status_code=500,
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
