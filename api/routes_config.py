"""GET /api/config: runtime settings for the web app, so nothing secret-ish is baked into the build.

Today it carries only the browser RUM settings for Dash0's web SDK (web/src/rum.ts):
`{"rum": {"endpoint", "token", "service_name", "environment", "version"}}`, or `{"rum": null}`
when `DASH0_WEB_TOKEN` is unset, in which case the browser sends no telemetry.

`DASH0_WEB_TOKEN` is a Dash0 auth token limited to "Ingesting" on one dataset. Dash0 designs
these website tokens to be public in the page; we still keep it out of git and out of the image,
and it never goes on a span or in a log line.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from opentelemetry import trace

from api import telemetry

WEB_SERVICE = "debrief-web"
DEFAULT_WEB_ENDPOINT = "https://ingress.us-west-2.aws.dash0.com"

router = APIRouter(prefix="/api", tags=["config"])
tracer = trace.get_tracer("debrief")


def rum_config() -> dict[str, str] | None:
    token = os.environ.get("DASH0_WEB_TOKEN", "").strip()
    if not token:
        return None
    endpoint = os.environ.get("DASH0_WEB_ENDPOINT", "").strip() or DEFAULT_WEB_ENDPOINT
    return {
        "endpoint": endpoint.rstrip("/"),
        "token": token,
        "service_name": WEB_SERVICE,
        "environment": telemetry.deployment_environment(),
        "version": telemetry.app_version(),
    }


@router.get("/config")
def config() -> dict[str, Any]:
    with tracer.start_as_current_span("api.config") as span:
        rum = rum_config()
        span.set_attribute("debrief.rum.enabled", rum is not None)
        return {"rum": rum}
