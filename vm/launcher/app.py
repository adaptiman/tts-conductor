# SPDX-License-Identifier: CC-BY-NC-SA-4.0

"""VM launcher service for starting and stopping the bot container on demand."""

import base64
import binascii
import hashlib
import hmac
import json
import os
import shlex
from dataclasses import dataclass
from typing import Any

import docker
from docker.errors import APIError, DockerException, NotFound
from dotenv import dotenv_values
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

ACTIVE_CONTAINER_STATUSES = {
    "created",
    "running",
    "restarting",
    "paused",
}
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
DEFAULT_BOT_IMAGE = "tts-conductor:local"
DEFAULT_BOT_COMMAND = "python ip_conductor.py --voice --voice-transport daily --headless"
DEFAULT_BOT_CONTAINER_NAME = "tts-conductor-bot"
DEFAULT_BOT_ENV_FILE = "/run/bot-env/.env"
DEFAULT_BOT_NETWORK = "tts-conductor-bot-net"
DEFAULT_DAILY_HOOK_START_ON_UNRECOGNIZED_EVENT = True

app = FastAPI(title="tts-conductor-vm-launcher", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
_client = docker.from_env()


@dataclass(frozen=True)
class LauncherConfig:
    shared_secret: str
    bot_image: str
    bot_command: str
    bot_container_name: str
    bot_env_file: str
    bot_network: str
    bot_pull_on_start: bool
    bot_memory_limit: str
    bot_nano_cpus: int | None
    hook_shared_secret: str
    hook_hmac_secret: str
    webhook_room_name: str
    stop_via_webhook_enabled: bool
    start_on_unrecognized_event: bool


def _load_config() -> LauncherConfig:
    shared_secret = os.getenv("LAUNCHER_SHARED_SECRET", "").strip()
    if not shared_secret:
        raise RuntimeError("Missing required env var LAUNCHER_SHARED_SECRET")

    bot_nano_cpus_raw = os.getenv("BOT_NANO_CPUS", "").strip()
    bot_nano_cpus: int | None = None
    if bot_nano_cpus_raw:
        bot_nano_cpus = int(bot_nano_cpus_raw)

    return LauncherConfig(
        shared_secret=shared_secret,
        bot_image=os.getenv("BOT_IMAGE", DEFAULT_BOT_IMAGE).strip(),
        bot_command=os.getenv("BOT_COMMAND", DEFAULT_BOT_COMMAND).strip(),
        bot_container_name=os.getenv(
            "BOT_CONTAINER_NAME", DEFAULT_BOT_CONTAINER_NAME
        ).strip(),
        bot_env_file=os.getenv("BOT_ENV_FILE", DEFAULT_BOT_ENV_FILE).strip(),
        bot_network=os.getenv("BOT_NETWORK", DEFAULT_BOT_NETWORK).strip(),
        bot_pull_on_start=_as_bool(os.getenv("BOT_PULL_ON_START", "false")),
        bot_memory_limit=os.getenv("BOT_MEMORY_LIMIT", "").strip(),
        bot_nano_cpus=bot_nano_cpus,
        hook_shared_secret=os.getenv("DAILY_HOOK_SHARED_SECRET", "").strip(),
        hook_hmac_secret=os.getenv("DAILY_HOOK_HMAC_SECRET", "").strip(),
        webhook_room_name=os.getenv("DAILY_WEBHOOK_ROOM_NAME", "").strip(),
        stop_via_webhook_enabled=_as_bool(
            os.getenv("DAILY_HOOK_ENABLE_STOP_ACTION", "false")
        ),
        start_on_unrecognized_event=_as_bool(
            os.getenv(
                "DAILY_HOOK_START_ON_UNRECOGNIZED_EVENT",
                "true" if DEFAULT_DAILY_HOOK_START_ON_UNRECOGNIZED_EVENT else "false",
            )
        ),
    )


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _authorize(provided_secret: str | None, expected_secret: str) -> None:
    provided = (provided_secret or "").strip()
    if not provided or not hmac.compare_digest(provided, expected_secret):
        raise HTTPException(status_code=401, detail="Missing or invalid launcher secret")


def _ensure_network(network_name: str) -> None:
    try:
        _client.networks.get(network_name)
    except NotFound:
        _client.networks.create(name=network_name, driver="bridge")


def _get_container(container_name: str):
    try:
        return _client.containers.get(container_name)
    except NotFound:
        return None


def _container_payload(container) -> dict[str, Any]:
    container.reload()
    state = container.attrs.get("State", {})
    image_tags = container.image.tags
    image_ref = image_tags[0] if image_tags else container.image.id
    return {
        "name": container.name,
        "id": container.short_id,
        "status": container.status,
        "image": image_ref,
        "startedAt": state.get("StartedAt"),
        "finishedAt": state.get("FinishedAt"),
    }


def _load_bot_environment(env_file_path: str) -> dict[str, str]:
    env_values = dotenv_values(env_file_path)
    prepared: dict[str, str] = {}
    for key, value in env_values.items():
        if not key or value is None:
            continue
        prepared[key] = str(value)
    return prepared


def _is_active_status(container_status: str) -> bool:
    return container_status in ACTIVE_CONTAINER_STATUSES


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


def _extract_first_non_owner_join(payload: dict[str, Any]) -> bool | None:
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


def _provided_hook_secret(
    request: Request,
    payload: dict[str, Any],
    query_secret: str,
) -> str:
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

    if query_secret:
        return query_secret.strip()

    payload_secret = payload.get("secret")
    if isinstance(payload_secret, str):
        return payload_secret.strip()

    return ""


def _verify_daily_hmac(raw_body: bytes, request: Request, hmac_secret: str) -> bool:
    try:
        key_bytes = base64.b64decode(hmac_secret)
    except (ValueError, binascii.Error):
        key_bytes = hmac_secret.encode()

    # Current Daily webhooks include X-Webhook-Timestamp and X-Webhook-Signature.
    webhook_sig = request.headers.get("x-webhook-signature", "").strip()
    webhook_ts = request.headers.get("x-webhook-timestamp", "").strip()
    if webhook_sig and webhook_ts:
        expected_sig = (
            webhook_sig[7:] if webhook_sig.lower().startswith("sha256=") else webhook_sig
        )
        signed_payload = webhook_ts.encode() + b"." + raw_body
        computed_b64 = base64.b64encode(
            hmac.new(key_bytes, signed_payload, hashlib.sha256).digest()
        ).decode("ascii")
        if hmac.compare_digest(computed_b64, expected_sig):
            return True

    # Backward-compatible support for legacy x-daily-signature format.
    sig_header = request.headers.get("x-daily-signature", "").strip()
    if not sig_header:
        return False

    sig_b64 = sig_header[7:] if sig_header.lower().startswith("sha256=") else sig_header
    try:
        expected_digest = base64.b64decode(sig_b64)
    except (ValueError, binascii.Error):
        return False

    computed = hmac.new(key_bytes, raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(computed, expected_digest)


def _is_daily_hook_authorized(
    request: Request,
    payload: dict[str, Any],
    config: LauncherConfig,
    raw_body: bytes,
    query_secret: str,
) -> bool:
    if config.hook_hmac_secret:
        return _verify_daily_hmac(raw_body, request, config.hook_hmac_secret)

    if not config.hook_shared_secret:
        return True

    provided = _provided_hook_secret(request, payload, query_secret)
    if not provided:
        return False
    return hmac.compare_digest(provided, config.hook_shared_secret)


def _resolve_daily_hook_action(
    requested_action: str,
    payload: dict[str, Any],
    config: LauncherConfig,
) -> tuple[str, str]:
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


async def _request_payload(request: Request) -> tuple[dict[str, Any], bytes]:
    raw_body = await request.body()
    if not raw_body:
        return {}, b""

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        return {}, raw_body

    if isinstance(parsed, dict):
        return parsed, raw_body
    return {"value": parsed}, raw_body


def _status_payload(config: LauncherConfig) -> dict[str, Any]:
    container = _get_container(config.bot_container_name)
    if container is None:
        return {
            "ok": True,
            "running": False,
            "reason": "container_not_found",
            "containerName": config.bot_container_name,
        }

    payload = _container_payload(container)
    payload["running"] = _is_active_status(payload["status"])
    payload["containerName"] = config.bot_container_name
    payload["network"] = config.bot_network
    payload["ok"] = True
    return payload


def _launch_container(config: LauncherConfig) -> dict[str, Any]:
    try:
        existing = _get_container(config.bot_container_name)
        if existing is not None:
            existing_payload = _container_payload(existing)
            if _is_active_status(existing_payload["status"]):
                return {
                    "ok": True,
                    "started": False,
                    "reason": "container_already_active",
                    "container": existing_payload,
                }
            existing.remove(force=True)

        _ensure_network(config.bot_network)

        if config.bot_pull_on_start:
            _client.images.pull(config.bot_image)

        bot_environment = _load_bot_environment(config.bot_env_file)
        run_kwargs: dict[str, Any] = {
            "image": config.bot_image,
            "command": shlex.split(config.bot_command),
            "name": config.bot_container_name,
            "detach": True,
            "environment": bot_environment,
            "network": config.bot_network,
            "restart_policy": {"Name": "no"},
            "labels": {
                "managed-by": "tts-conductor-vm-launcher",
                "app": "tts-conductor",
            },
            "init": True,
        }

        if config.bot_memory_limit:
            run_kwargs["mem_limit"] = config.bot_memory_limit
        if config.bot_nano_cpus is not None:
            run_kwargs["nano_cpus"] = config.bot_nano_cpus

        container = _client.containers.run(**run_kwargs)
        return {
            "ok": True,
            "started": True,
            "container": _container_payload(container),
            "network": config.bot_network,
        }
    except (APIError, DockerException, OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc


def _stop_container(config: LauncherConfig) -> dict[str, Any]:
    try:
        container = _get_container(config.bot_container_name)
        if container is None:
            return {
                "ok": True,
                "stopped": False,
                "reason": "container_not_found",
                "containerName": config.bot_container_name,
            }

        container_payload = _container_payload(container)
        was_active = _is_active_status(container_payload["status"])
        if was_active:
            container.stop(timeout=20)

        container.remove(force=True)
        return {
            "ok": True,
            "stopped": was_active,
            "removed": True,
            "containerName": config.bot_container_name,
        }
    except (APIError, DockerException, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/status")
def status(x_job_launcher_secret: str | None = Header(default=None)) -> dict[str, Any]:
    config = _load_config()
    _authorize(x_job_launcher_secret, config.shared_secret)
    return _status_payload(config)


@app.post("/launch")
def launch(x_job_launcher_secret: str | None = Header(default=None)) -> dict[str, Any]:
    config = _load_config()
    _authorize(x_job_launcher_secret, config.shared_secret)
    return _launch_container(config)


@app.post("/stop")
def stop(x_job_launcher_secret: str | None = Header(default=None)) -> dict[str, Any]:
    config = _load_config()
    _authorize(x_job_launcher_secret, config.shared_secret)
    return _stop_container(config)


@app.post("/daily-hook")
async def daily_hook(
    request: Request,
    action: str = Query(default=""),
    secret: str = Query(default=""),
) -> dict[str, Any]:
    config = _load_config()
    payload, raw_body = await _request_payload(request)

    if not _is_daily_hook_authorized(request, payload, config, raw_body, secret):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Daily webhook secret/signature",
        )

    incoming_room = _extract_room_name(payload)
    if config.webhook_room_name:
        if not incoming_room:
            return {
                "ok": True,
                "handled": False,
                "reason": "room_missing_in_payload",
                "configuredRoom": config.webhook_room_name,
            }
        if not _rooms_match(config.webhook_room_name, incoming_room):
            return {
                "ok": True,
                "handled": False,
                "reason": "room_mismatch",
                "configuredRoom": config.webhook_room_name,
                "incomingRoom": incoming_room,
            }

    requested_action = action.strip().lower()
    resolved_action, action_source = _resolve_daily_hook_action(
        requested_action, payload, config
    )
    if not resolved_action and config.start_on_unrecognized_event:
        resolved_action = "start"
        action_source = f"fallback:{action_source or 'unrecognized_event'}"

    if not resolved_action:
        return {
            "ok": True,
            "handled": False,
            "reason": "unsupported_event",
            "actionSource": action_source,
        }

    if resolved_action == "start":
        launcher_result = _launch_container(config)
        return {
            "ok": True,
            "handled": True,
            "action": "start",
            "actionSource": action_source,
            "launcherResponse": launcher_result,
            "started": bool(launcher_result.get("started", False)),
        }

    launcher_result = _stop_container(config)
    return {
        "ok": True,
        "handled": True,
        "action": "stop",
        "actionSource": action_source,
        "launcherResponse": launcher_result,
        "stopped": bool(launcher_result.get("stopped", False)),
    }


@app.options("/daily-hook")
def daily_hook_options() -> dict[str, Any]:
    return {"ok": True, "allowed": ["OPTIONS", "POST"]}
