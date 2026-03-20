"""VM launcher service for starting and stopping the bot container on demand."""

import hmac
import os
import shlex
from dataclasses import dataclass
from typing import Any

import docker
from docker.errors import APIError, DockerException, NotFound
from dotenv import dotenv_values
from fastapi import FastAPI, Header, HTTPException

ACTIVE_CONTAINER_STATUSES = {
    "created",
    "running",
    "restarting",
    "paused",
}
DEFAULT_BOT_IMAGE = "acrttsconductorprod.azurecr.io/tts-conductor:latest"
DEFAULT_BOT_COMMAND = "python ip_conductor.py --voice --voice-transport daily --headless"
DEFAULT_BOT_CONTAINER_NAME = "tts-conductor-bot"
DEFAULT_BOT_ENV_FILE = "/run/bot-env/.env"
DEFAULT_BOT_NETWORK = "tts-conductor-bot-net"

app = FastAPI(title="tts-conductor-vm-launcher", version="1.0.0")
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
        bot_pull_on_start=_as_bool(os.getenv("BOT_PULL_ON_START", "true")),
        bot_memory_limit=os.getenv("BOT_MEMORY_LIMIT", "").strip(),
        bot_nano_cpus=bot_nano_cpus,
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


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/status")
def status(x_job_launcher_secret: str | None = Header(default=None)) -> dict[str, Any]:
    config = _load_config()
    _authorize(x_job_launcher_secret, config.shared_secret)

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


@app.post("/launch")
def launch(x_job_launcher_secret: str | None = Header(default=None)) -> dict[str, Any]:
    config = _load_config()
    _authorize(x_job_launcher_secret, config.shared_secret)

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


@app.post("/stop")
def stop(x_job_launcher_secret: str | None = Header(default=None)) -> dict[str, Any]:
    config = _load_config()
    _authorize(x_job_launcher_secret, config.shared_secret)

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
