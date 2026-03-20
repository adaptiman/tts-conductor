"""Azure Function: Launcher status endpoint."""

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


def _get_arm_token() -> str:
    """Obtain an ARM access token from the Managed Identity endpoint."""
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


def _json_response(payload: dict[str, Any], status_code: int) -> Any:
    return func.HttpResponse(
        body=json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


def main(req: func.HttpRequest) -> func.HttpResponse:
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
