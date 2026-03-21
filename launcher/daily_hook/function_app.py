"""Azure Function: Daily webhook lifecycle handler for the VM launcher.

This endpoint validates Daily webhooks, maps start/stop events, and then calls
the VM launcher service over HTTPS.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import azure.functions as func
import requests

AUTH_HEADER_NAME = "x-job-launcher-secret"
DEFAULT_LAUNCHER_TIMEOUT_SECONDS = 15.0
START_EVENT_NAMES = {
    "meeting.started",
    "meeting_started",
    "meeting-started",
    "participant.joined",
    "participant_joined",
    "participant-joined",
    "waiting-participant.joined",
    "waiting_participant_joined",
    "waiting-participant-joined",
}
STOP_EVENT_NAMES = {
    "participant.left",
    "participant_left",
    "participant-left",
    "meeting.ended",
    "meeting_ended",
    "meeting-ended",
    "waiting-participant.left",
    "waiting_participant_left",
    "waiting-participant-left",
}


@dataclass(frozen=True)
class HookConfig:
    launcher_base_url: str
    launcher_shared_secret: str
    hook_shared_secret: str
    hook_hmac_secret: str
    webhook_room_name: str
    launcher_timeout_seconds: float
    stop_via_webhook_enabled: bool
    start_on_unrecognized_event: bool


def _load_config() -> HookConfig:
    missing: list[str] = []

    launcher_base_url = os.getenv("VM_LAUNCHER_BASE_URL", "").strip()
    if not launcher_base_url:
        missing.append("VM_LAUNCHER_BASE_URL")

    launcher_shared_secret = os.getenv("JOB_LAUNCHER_SHARED_SECRET", "").strip()
    if not launcher_shared_secret:
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

    return HookConfig(
        launcher_base_url=launcher_base_url.rstrip("/"),
        launcher_shared_secret=launcher_shared_secret,
        hook_shared_secret=os.getenv("DAILY_HOOK_SHARED_SECRET", "").strip(),
        hook_hmac_secret=os.getenv("DAILY_HOOK_HMAC_SECRET", "").strip(),
        webhook_room_name=os.getenv("DAILY_WEBHOOK_ROOM_NAME", "").strip(),
        launcher_timeout_seconds=timeout_seconds,
        stop_via_webhook_enabled=_as_bool(
            os.getenv("DAILY_HOOK_ENABLE_STOP_ACTION", "false")
        ),
        start_on_unrecognized_event=_as_bool(
            os.getenv("DAILY_HOOK_START_ON_UNRECOGNIZED_EVENT", "true")
        ),
    )


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _json_response(payload: dict[str, Any], status_code: int) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


def _provided_hook_secret(request: func.HttpRequest, payload: dict[str, Any]) -> str:
    header_value = request.headers.get("x-daily-hook-secret") or request.headers.get(
        "x-webhook-secret"
    )
    if header_value:
        return header_value.strip()

    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if auth_header:
        return auth_header

    query_secret = request.params.get("secret", "").strip()
    if query_secret:
        return query_secret

    payload_secret = payload.get("secret")
    if isinstance(payload_secret, str):
        return payload_secret.strip()

    return ""


def _verify_daily_hmac(request: func.HttpRequest, hmac_secret: str) -> bool:
    """Verify Daily's x-daily-signature header (HMAC-SHA256 of raw body)."""
    sig_header = request.headers.get("x-daily-signature", "").strip()
    if not sig_header:
        return False

    sig_b64 = sig_header[7:] if sig_header.lower().startswith("sha256=") else sig_header
    try:
        expected_digest = base64.b64decode(sig_b64)
    except (ValueError, binascii.Error):
        return False

    try:
        key_bytes = base64.b64decode(hmac_secret)
    except (ValueError, binascii.Error):
        key_bytes = hmac_secret.encode()

    raw_body = request.get_body()
    computed = hmac.new(key_bytes, raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(computed, expected_digest)


def _is_authorized(
    request: func.HttpRequest,
    payload: dict[str, Any],
    config: HookConfig,
) -> bool:
    if config.hook_hmac_secret:
        return _verify_daily_hmac(request, config.hook_hmac_secret)

    if not config.hook_shared_secret:
        return True

    provided = _provided_hook_secret(request, payload)
    if not provided:
        return False
    return hmac.compare_digest(provided, config.hook_shared_secret)


def _extract_event_from_container(container: dict[str, Any]) -> str:
    for key in ("event", "eventType", "event_type", "type", "name"):
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested_key in ("type", "event", "name", "eventType", "event_type"):
                nested_value = value.get(nested_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
    return ""


def _extract_event_name(payload: dict[str, Any]) -> str:
    direct = _extract_event_from_container(payload)
    if direct:
        return direct

    for key in ("data", "payload", "meeting", "request"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidate = _extract_event_from_container(nested)
            if candidate:
                return candidate

    return ""


def _extract_first_non_owner_join(payload: dict[str, Any]) -> Optional[bool]:
    containers: list[dict[str, Any]] = [payload]
    for key in ("data", "payload", "meeting", "participant"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            containers.append(nested)

    for container in containers:
        value = container.get("first_non_owner_join")
        if isinstance(value, bool):
            return value

    return None


def _room_name_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "room", "id"):
            nested_value = value.get(key)
            if isinstance(nested_value, str) and nested_value.strip():
                return nested_value.strip()
    return ""


def _extract_room_name(payload: dict[str, Any]) -> str:
    for key in ("room", "room_name", "roomName"):
        room = _room_name_from_value(payload.get(key))
        if room:
            return room

    for parent_key in ("meeting", "data", "payload"):
        nested = payload.get(parent_key)
        if not isinstance(nested, dict):
            continue
        for key in ("room", "room_name", "roomName", "name"):
            room = _room_name_from_value(nested.get(key))
            if room:
                return room

    return ""


def _rooms_match(configured_room: str, incoming_room: str) -> bool:
    configured = configured_room.strip().rstrip("/").lower()
    incoming = incoming_room.strip().rstrip("/").lower()
    if not configured or not incoming:
        return False
    if configured == incoming:
        return True

    configured_tail = configured.split("/")[-1]
    incoming_tail = incoming.split("/")[-1]
    return configured_tail == incoming_tail


def _resolve_action(
    request: func.HttpRequest,
    payload: dict[str, Any],
    config: HookConfig,
) -> tuple[str, str]:
    requested_action = request.params.get("action", "").strip().lower()
    if requested_action in {"start", "stop"}:
        return requested_action, f"query:{requested_action}"

    event_name = _extract_event_name(payload)
    normalized_event = event_name.strip().lower().replace(" ", "")
    if normalized_event in START_EVENT_NAMES:
        return "start", event_name
    if config.stop_via_webhook_enabled and normalized_event in STOP_EVENT_NAMES:
        return "stop", event_name

    first_non_owner_join = _extract_first_non_owner_join(payload)
    if first_non_owner_join is True:
        return "start", "first_non_owner_join"

    return "", event_name


def _launcher_url(config: HookConfig, action: str) -> str:
    route_map = {
        "start": "/launch",
        "stop": "/stop",
        "status": "/status",
    }
    return f"{config.launcher_base_url}{route_map[action]}"


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


def _call_vm_launcher(
    config: HookConfig,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = _launcher_url(config, action)
    headers = {
        AUTH_HEADER_NAME: config.launcher_shared_secret,
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=config.launcher_timeout_seconds,
    )
    body = _parse_response_body(response)
    response.raise_for_status()
    return {
        "statusCode": response.status_code,
        "body": body,
    }


def _start_if_needed(
    config: HookConfig,
    action_source: str,
    incoming_room: str,
) -> dict[str, Any]:
    result = _call_vm_launcher(
        config,
        "start",
        {
            "source": "daily-hook",
            "actionSource": action_source,
            "incomingRoom": incoming_room,
        },
    )
    body = result["body"]
    payload: dict[str, Any] = {
        "launcherStatusCode": result["statusCode"],
        "launcherResponse": body,
    }
    started = body.get("started")
    if isinstance(started, bool):
        payload["started"] = started
    return payload


def _stop_active(
    config: HookConfig,
    action_source: str,
    incoming_room: str,
) -> dict[str, Any]:
    result = _call_vm_launcher(
        config,
        "stop",
        {
            "source": "daily-hook",
            "actionSource": action_source,
            "incomingRoom": incoming_room,
        },
    )
    body = result["body"]
    payload: dict[str, Any] = {
        "launcherStatusCode": result["statusCode"],
        "launcherResponse": body,
    }
    stopped = body.get("stopped")
    if isinstance(stopped, bool):
        payload["stopped"] = stopped
    return payload


def _response_preview(response: Optional[requests.Response]) -> str:
    if response is None:
        return ""
    text = (response.text or "").strip()
    return text[:500]


def main(req: func.HttpRequest) -> func.HttpResponse:
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

    try:
        payload = req.get_json()
    except ValueError:
        payload = {}

    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    if not _is_authorized(req, payload, config):
        return _json_response(
            {
                "ok": False,
                "error": "unauthorized",
                "message": "Missing or invalid Daily webhook secret.",
            },
            status_code=401,
        )

    incoming_room = _extract_room_name(payload)
    if config.webhook_room_name:
        if not incoming_room:
            return _json_response(
                {
                    "ok": True,
                    "handled": False,
                    "reason": "room_missing_in_payload",
                    "configuredRoom": config.webhook_room_name,
                },
                status_code=202,
            )
        if not _rooms_match(config.webhook_room_name, incoming_room):
            return _json_response(
                {
                    "ok": True,
                    "handled": False,
                    "reason": "room_mismatch",
                    "configuredRoom": config.webhook_room_name,
                    "incomingRoom": incoming_room,
                },
                status_code=202,
            )

    action, action_source = _resolve_action(req, payload, config)
    if not action and config.start_on_unrecognized_event:
        action = "start"
        action_source = f"fallback:{action_source or 'unrecognized_event'}"

    if not action:
        return _json_response(
            {
                "ok": True,
                "handled": False,
                "reason": "unsupported_event",
                "actionSource": action_source,
            },
            status_code=202,
        )

    try:
        if action == "start":
            result = _start_if_needed(config, action_source, incoming_room)
            return _json_response(
                {
                    "ok": True,
                    "handled": True,
                    "action": "start",
                    "actionSource": action_source,
                    "launcherBaseUrl": config.launcher_base_url,
                    **result,
                },
                status_code=202,
            )

        result = _stop_active(config, action_source, incoming_room)
        return _json_response(
            {
                "ok": True,
                "handled": True,
                "action": "stop",
                "actionSource": action_source,
                "launcherBaseUrl": config.launcher_base_url,
                **result,
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
