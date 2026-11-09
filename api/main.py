"""Debrief API: FastAPI app. Routes are added task by task (see docs/BRIEF.md)."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api import llm, routes_memo, routes_playlists, routes_search, routes_stack, telemetry

# Set by the image build (the commit's short SHA); "dev" locally.
APP_VERSION = os.environ.get("APP_VERSION", "dev")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

# Traces, metrics and logs to Dash0 (task 11). Exports only when OTEL_EXPORTER_OTLP_ENDPOINT
# is set; probes are not traced.
telemetry.setup(telemetry.API_SERVICE)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Import and build the model SDK client before the first request. Done lazily inside
    # the first model call, it showed up in production as ~3.3 s before the HTTP request.
    llm.warm()
    yield


app = FastAPI(title="Debrief", version=APP_VERSION, lifespan=lifespan)
telemetry.instrument_app(app)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/readyz")
def readyz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/version")
def version() -> dict[str, str]:
    return {"version": APP_VERSION}


app.include_router(routes_search.router)
app.include_router(routes_stack.router)
app.include_router(routes_memo.router)
app.include_router(routes_playlists.router)

# Keep this at the end of the file: a mount at "/" matches every path, so routes
# registered after it would never be reached. Skipped when the web app isn't
# built (local dev and unit tests).
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
