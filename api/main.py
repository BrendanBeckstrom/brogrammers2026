"""Debrief API: FastAPI app. Routes are added task by task (see docs/BRIEF.md)."""

from fastapi import FastAPI

app = FastAPI(title="Debrief")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
