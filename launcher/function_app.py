"""Azure Function launcher for on-demand Container Apps Job executions.

This endpoint starts the voice bot job only when no execution is already active,
which prevents duplicate bot instances for the same room/session window.
"""

from __future__ import annotations

import hmac
import importlib
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import requests

func = importlib.import_module("azure.functions")
ManagedIdentityCredential = getattr(
    importlib.import_module("azure.identity"), "ManagedIdentityCredential"
)

ARM_SCOPE = "https://management.azure.com/.default"
ARM_API_VERSION = "2023-05-01"
AUTH_HEADER_NAME = "x-job-launcher-secret"
ACTIVE_EXECUTION_STATUSES = {
    "Running",
    "InProgress",
    "Provisioning",
    "Pending",
    "Queued",
}

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@dataclass(frozen=True)
class LauncherConfig:
    subscription_id: str
    resource_group: str
    job_name: str
    shared_secret: str


def _load_config() -> LauncherConfig:
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

    shared_secret = os.getenv("JOB_LAUNCHER_SHARED_SECRET", "").strip()
    if not shared_secret:
        missing.append("JOB_LAUNCHER_SHARED_SECRET")

    if missing:
        raise RuntimeError(
            "Launcher is missing required environment variables: " + ", ".join(missing)
        )

    return LauncherConfig(
        subscription_id=subscription_id,
        resource_group=resource_group,
        job_name=job_name,
        shared_secret=shared_secret,
    )


def _management_base_url(config: LauncherConfig) -> str:
    return (
        "https://management.azure.com/subscriptions/"
        f"{config.subscription_id}/resourceGroups/{config.resource_group}"
        f"/providers/Microsoft.App/jobs/{config.job_name}"
    )


def _management_headers() -> dict[str, str]:
    credential = ManagedIdentityCredential()
    token = credential.get_token(ARM_SCOPE)
    return {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
    }


def _list_job_executions(config: LauncherConfig) -> list[dict[str, Any]]:
    url = (
        f"{_management_base_url(config)}/executions"
        f"?api-version={ARM_API_VERSION}"
    )
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


def _latest_active_execution(
    executions: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    active = [item for item in executions if _is_execution_active(item)]
    if not active:
        return None

    def _start_time_key(item: dict[str, Any]) -> str:
        properties = item.get("properties") or {}
        return str(properties.get("startTime") or "")

    active.sort(key=_start_time_key, reverse=True)
    return active[0]


def _start_job_execution(config: LauncherConfig) -> dict[str, Any]:
    url = f"{_management_base_url(config)}/start?api-version={ARM_API_VERSION}"
    response = requests.post(url, headers=_management_headers(), json={}, timeout=20)
    response.raise_for_status()
    return response.json()


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


@app.route(route="launch", methods=["POST"])
def launch_job(request: Any) -> Any:
    """Start one job execution if no active execution already exists."""
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

    if not _is_authorized(request, config):
        return _json_response(
            {
                "ok": False,
                "error": "unauthorized",
                "message": f"Missing or invalid {AUTH_HEADER_NAME} header.",
            },
            status_code=401,
        )

    try:
        executions = _list_job_executions(config)
        active_execution = _latest_active_execution(executions)

        if active_execution is not None:
            properties = active_execution.get("properties") or {}
            return _json_response(
                {
                    "ok": True,
                    "started": False,
                    "reason": "execution_already_active",
                    "job": config.job_name,
                    "execution": {
                        "name": active_execution.get("name"),
                        "status": properties.get("status"),
                        "startTime": properties.get("startTime"),
                    },
                },
                status_code=202,
            )

        started = _start_job_execution(config)
        return _json_response(
            {
                "ok": True,
                "started": True,
                "job": config.job_name,
                "execution": {
                    "name": started.get("name"),
                    "id": started.get("id"),
                },
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


@app.route(route="status", methods=["GET"])
def launcher_status(_request: Any) -> Any:
    """Quick diagnostics endpoint for launcher health and active execution."""
    try:
        config = _load_config()
        executions = _list_job_executions(config)
        active_execution = _latest_active_execution(executions)
        active_payload: dict[str, Any] | None = None

        if active_execution is not None:
            properties = active_execution.get("properties") or {}
            active_payload = {
                "name": active_execution.get("name"),
                "status": properties.get("status"),
                "startTime": properties.get("startTime"),
            }

        return _json_response(
            {
                "ok": True,
                "job": config.job_name,
                "activeExecution": active_payload,
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
    except requests.RequestException as exc:
        return _json_response(
            {
                "ok": False,
                "error": "azure_api_unreachable",
                "message": str(exc),
            },
            status_code=502,
        )
