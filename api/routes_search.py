"""Search routes (tasks 6 and 8): /api/playlist, /api/tools, /api/talks, /api/clips/{id}.

Each route runs inside its own OpenTelemetry span (ids, counts and timings only) and
gets its Elastic client through `search_client` and its model through `model_client`;
tests override both with fakes. With DEBRIEF_CACHE=on each route first asks the demo cache
(api/demo_cache.py, task 13).
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from opentelemetry import trace
from pydantic import BaseModel, Field

from api import demo_cache, llm, search, telemetry
from api.llm import LLMClient
from api.search import SearchBackendError, SearchClient

router = APIRouter(prefix="/api")
tracer = trace.get_tracer("debrief")
log = logging.getLogger("debrief.api")


class PlaylistBody(BaseModel):
    tools: list[str] = Field(default_factory=list, max_length=50)
    pain: str | None = Field(default=None, max_length=500)
    attended_talk_ids: list[str] = Field(default_factory=list, max_length=500)
    filter: Literal["all", "attended", "missed"] = "all"
    budget_minutes: int = Field(default=25, ge=1, le=120)
    rerank: bool = True


def search_client() -> SearchClient:
    """The Elastic client; a FastAPI dependency so tests can swap in a fake."""
    try:
        return search.get_client()
    except SearchBackendError as e:
        if demo_cache.enabled():
            # Offline demo: cached requests never touch Elastic; a miss fails on first use.
            return demo_cache.OfflineSearchClient()
        raise HTTPException(status_code=503, detail="Search is not configured") from e


Client = Annotated[SearchClient, Depends(search_client)]


current_trace_id = telemetry.current_trace_id


def metric_tools(tools: list[str]) -> list[str]:
    """Requested tool ids for metric attributes: known ids as-is, anything else "other"."""
    catalog = search.tool_catalog()
    ids = dict.fromkeys(t.strip() for t in tools if t.strip())
    return [t if t in catalog else "other" for t in ids]


def model_client() -> LLMClient:
    """The language model (api/llm.py); a FastAPI dependency so tests can swap in a fake."""
    return llm.get_llm()


Model = Annotated[LLMClient, Depends(model_client)]


def _backend_error(e: SearchBackendError) -> HTTPException:
    if demo_cache.enabled():
        return demo_cache.miss_error(str(e))
    return HTTPException(status_code=502, detail=str(e))


@router.post("/playlist")
def playlist(
    body: PlaylistBody, client: Client, model: Model, response: Response
) -> dict[str, Any]:
    with tracer.start_as_current_span("api.playlist") as span:
        span.set_attributes(
            {
                "debrief.tools": body.tools,
                "debrief.filter": body.filter,
                "debrief.attended_count": len(body.attended_talk_ids),
                "debrief.budget_minutes": body.budget_minutes,
                "debrief.rerank_requested": body.rerank,
                "debrief.pain_present": bool((body.pain or "").strip()),
            }
        )
        cached = demo_cache.serve("playlist", body.model_dump(mode="json"), span, response)
        if cached is not None:
            span.set_attribute("debrief.clips", len(cached.get("clips") or []))
            return {**cached, "trace_id": current_trace_id()}
        request = search.PlaylistRequest(
            tools=body.tools,
            pain=body.pain,
            attended_talk_ids=body.attended_talk_ids,
            filter=body.filter,
            budget_minutes=body.budget_minutes,
            rerank=body.rerank,
        )
        started = time.perf_counter()
        tool_ids = metric_tools(body.tools)
        try:
            result = search.build_playlist(client, request, model)
        except SearchBackendError as e:
            elapsed = (time.perf_counter() - started) * 1000
            telemetry.record_playlist(elapsed, 0, tool_ids, body.filter, "error")
            raise _backend_error(e) from e
        elapsed = (time.perf_counter() - started) * 1000
        clips = len(result["clips"])
        outcome = "ok" if clips else "empty"
        telemetry.record_playlist(elapsed, clips, tool_ids, body.filter, outcome)
        span.set_attribute("debrief.clips", clips)
        span.set_attribute("debrief.total_seconds", result["total_seconds"])
        if not clips:
            # A coverage gap organizers want to see: ids and counts only, never the pain text.
            log.info(
                "empty playlist",
                extra={
                    "debrief.tools": tool_ids,
                    "debrief.filter": body.filter,
                    "debrief.attended_count": len(body.attended_talk_ids),
                    "debrief.hits": result["debug"].get("hits", 0),
                },
            )
        return {
            "total_seconds": result["total_seconds"],
            "clips": result["clips"],
            "trace_id": current_trace_id(),
            "message": result["message"],
            "debug": result["debug"],
        }


@router.get("/tools")
def tools(client: Client, response: Response) -> list[dict[str, Any]]:
    with tracer.start_as_current_span("api.tools") as span:
        cached = demo_cache.serve("tools", None, span, response)
        if cached is not None:
            span.set_attribute("debrief.tools_count", len(cached))
            return cached
        try:
            rows = search.list_tools(client)
        except SearchBackendError as e:
            raise _backend_error(e) from e
        span.set_attribute("debrief.tools_count", len(rows))
        return rows


@router.get("/talks")
def talks(client: Client, response: Response) -> list[dict[str, Any]]:
    with tracer.start_as_current_span("api.talks") as span:
        cached = demo_cache.serve("talks", None, span, response)
        if cached is not None:
            span.set_attribute("debrief.talks_count", len(cached))
            return cached
        try:
            rows = search.list_talks(client)
        except SearchBackendError as e:
            raise _backend_error(e) from e
        span.set_attribute("debrief.talks_count", len(rows))
        return rows


@router.get("/clips/{clip_or_chunk_id}")
def clip(clip_or_chunk_id: str, client: Client, response: Response) -> dict[str, Any]:
    with tracer.start_as_current_span("api.clip") as span:
        span.set_attribute("debrief.clip_id", clip_or_chunk_id[:128])
        cached = demo_cache.serve("clips", {"clip_id": clip_or_chunk_id}, span, response)
        if cached is not None:
            return cached
        try:
            found = search.clip_text(client, clip_or_chunk_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail="Unknown clip id") from e
        except SearchBackendError as e:
            raise _backend_error(e) from e
        if found is None:
            raise HTTPException(status_code=404, detail="Unknown clip id")
        span.set_attribute("debrief.chunks", found["clip_id"].count("~") + 1)
        return found
