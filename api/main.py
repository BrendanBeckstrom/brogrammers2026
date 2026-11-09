"""Debrief API: FastAPI app. Routes are added task by task (see docs/BRIEF.md)."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api import routes_search, routes_stack

# Set by the image build (the commit's short SHA); "dev" locally.
APP_VERSION = os.environ.get("APP_VERSION", "dev")
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

app = FastAPI(title="Debrief", version=APP_VERSION)


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

# Keep this at the end of the file: a mount at "/" matches every path, so routes
# registered after it would never be reached. Skipped when the web app isn't
# built (local dev and unit tests).
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
