"""Azure Function: Daily webhook lifecycle handler for the bot job.

This endpoint translates Daily webhook events into Container Apps Job actions:
- Start bot when a meeting starts (or first non-owner joins via meeting_join_hook)
- Stop bot when participants leave / meeting ends
"""

import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import azure.functions as func
import requests

ARM_API_VERSION = "2023-05-01"
ACTIVE_EXECUTION_STATUSES = {
    "Running",
    "InProgress",
    "Provisioning",
    "Pending",
    "Queued",
}
START_EVENT_NAMES = {
    "meeting.started",
    "meeting_started",
    "meeting-started",
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
    subscription_id: str
    resource_group: str
    job_name: str
    hook_shared_secret: str
    webhook_room_name: str


def _load_config() -> HookConfig:
    missing: list[str] = []

    subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID", "").strip()
    if not subscription_id:
        missing.append("AZURE_SUBSCRIPTION_ID")

    resource_group = os.getenv("AZURE_RESOURCE_GROUP", "").strip()
    if not resource_group:
        missing.append("AZURE_RESOURCE_GROUP")

    job_name = os.getenv("AZURE_CONTAINER_JOB_NAME", "").strip()
    if not job_name:
        missing.append("AZURE_CONTAINER_JOB_NAME")

    if missing:
        raise RuntimeError(
            "Launcher is missing required environment variables: " + ", ".join(missing)
        )

    return HookConfig(
        subscription_id=subscription_id,
        resource_group=resource_group,
        job_name=job_name,
        hook_shared_secret=os.getenv("DAILY_HOOK_SHARED_SECRET", "").strip(),
        webhook_room_name=os.getenv("DAILY_WEBHOOK_ROOM_NAME", "").strip(),
    )


def _management_base_url(config: HookConfig) -> str:
    return (
        "https://management.azure.com/subscriptions/"
        f"{config.subscription_id}/resourceGroups/{config.resource_group}"
        f"/providers/Microsoft.App/jobs/{config.job_name}"
    )


def _get_arm_token() -> str:
    endpoint = os.environ["IDENTITY_ENDPOINT"]
    header_value = os.environ["IDENTITY_HEADER"]
    resp = requests.get(
        endpoint,
        params={
            "resource": "https://management.azure.com/",
            "api-version": "2019-08-01",
        },
        headers={"X-IDENTITY-HEADER": header_value},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _management_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_arm_token()}",
        "Content-Type": "application/json",
    }


def _list_job_executions(config: HookConfig) -> list[dict[str, Any]]:
    url = f"{_management_base_url(config)}/executions?api-version={ARM_API_VERSION}"
    response = requests.get(url, headers=_management_headers(), timeout=20)
    response.raise_for_status()
    payload = response.json()
    return payload.get("value", [])


def _is_execution_active(execution: dict[str, Any]) -> bool:
    properties = execution.get("properties") or {}
    status = str(properties.get("status") or "").strip()
    if not status:
        return False
    return status in ACTIVE_EXECUTION_STATUSES


def _active_executions(executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = [item for item in executions if _is_execution_active(item)]

    def _start_time_key(item: dict[str, Any]) -> str:
        properties = item.get("properties") or {}
        return str(properties.get("startTime") or "")

    active.sort(key=_start_time_key, reverse=True)
    return active


def _latest_active_execution(
    executions: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    active = _active_executions(executions)
    return active[0] if active else None


def _start_job_execution(config: HookConfig) -> dict[str, Any]:
    url = f"{_management_base_url(config)}/start?api-version={ARM_API_VERSION}"
    response = requests.post(url, headers=_management_headers(), json={}, timeout=20)
    response.raise_for_status()
    return response.json()


def _stop_job_execution(config: HookConfig, execution_name: str) -> dict[str, Any]:
    url = (
        f"{_management_base_url(config)}/stop/{execution_name}"
        f"?api-version={ARM_API_VERSION}"
    )
    response = requests.post(url, headers=_management_headers(), json=None, timeout=20)
    response.raise_for_status()
    if not response.text:
        return {}
    return response.json()


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


def _is_authorized(
    request: func.HttpRequest,
    payload: dict[str, Any],
    config: HookConfig,
) -> bool:
    # Keep hooks easy to test locally when no shared secret is configured.
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


def _resolve_action(request: func.HttpRequest, payload: dict[str, Any]) -> tuple[str, str]:
    requested_action = request.params.get("action", "").strip().lower()
    if requested_action in {"start", "stop"}:
        return requested_action, f"query:{requested_action}"

    event_name = _extract_event_name(payload)
    normalized_event = event_name.strip().lower().replace(" ", "")
    if normalized_event in START_EVENT_NAMES:
        return "start", event_name
    if normalized_event in STOP_EVENT_NAMES:
        return "stop", event_name

    first_non_owner_join = _extract_first_non_owner_join(payload)
    if first_non_owner_join is True:
        return "start", "first_non_owner_join"

    return "", event_name


def _start_if_needed(config: HookConfig) -> dict[str, Any]:
    executions = _list_job_executions(config)
    active_execution = _latest_active_execution(executions)

    if active_execution is not None:
        properties = active_execution.get("properties") or {}
        return {
            "started": False,
            "reason": "execution_already_active",
            "execution": {
                "name": active_execution.get("name"),
                "status": properties.get("status"),
                "startTime": properties.get("startTime"),
            },
        }

    started = _start_job_execution(config)
    return {
        "started": True,
        "execution": {
            "name": started.get("name"),
            "id": started.get("id"),
        },
    }


def _stop_active(config: HookConfig) -> dict[str, Any]:
    executions = _list_job_executions(config)
    active = _active_executions(executions)

    if not active:
        return {
            "stopped": False,
            "reason": "no_active_execution",
            "executionsStopped": [],
        }

    stopped_names: list[str] = []
    for execution in active:
        execution_name = str(execution.get("name") or "").strip()
        if not execution_name:
            continue
        _stop_job_execution(config, execution_name)
        stopped_names.append(execution_name)

    return {
        "stopped": bool(stopped_names),
        "executionsStopped": stopped_names,
        "activeCountBeforeStop": len(active),
    }


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

    action, action_source = _resolve_action(req, payload)
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
            result = _start_if_needed(config)
            return _json_response(
                {
                    "ok": True,
                    "handled": True,
                    "action": "start",
                    "actionSource": action_source,
                    "job": config.job_name,
                    **result,
                },
                status_code=202,
            )

        result = _stop_active(config)
        return _json_response(
            {
                "ok": True,
                "handled": True,
                "action": "stop",
                "actionSource": action_source,
                "job": config.job_name,
                **result,
            },
            status_code=202,
        )
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        body = exc.response.text if exc.response is not None else str(exc)
        return _json_response(
            {
                "ok": False,
                "error": "azure_api_error",
                "statusCode": status_code,
                "message": body[:500],
            },
            status_code=502,
        )
    except requests.RequestException as exc:
        return _json_response(
            {
                "ok": False,
                "error": "azure_api_unreachable",
                "message": str(exc),
            },
            status_code=502,
        )
