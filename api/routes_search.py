"""Search routes (tasks 6 and 8): /api/playlist, /api/tools, /api/talks, /api/clips/{id}.

Each route runs inside its own OpenTelemetry span (ids, counts and timings only) and
gets its Elastic client through `search_client` and its model through `model_client`;
tests override both with fakes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import trace
from pydantic import BaseModel, Field

from api import llm, search
from api.llm import LLMClient
from api.search import SearchBackendError, SearchClient

router = APIRouter(prefix="/api")
tracer = trace.get_tracer("debrief")


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
        raise HTTPException(status_code=503, detail="Search is not configured") from e


Client = Annotated[SearchClient, Depends(search_client)]


def model_client() -> LLMClient:
    """The language model (api/llm.py); a FastAPI dependency so tests can swap in a fake."""
    return llm.get_llm()


Model = Annotated[LLMClient, Depends(model_client)]


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def _backend_error(e: SearchBackendError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(e))


@router.post("/playlist")
def playlist(body: PlaylistBody, client: Client, model: Model) -> dict[str, Any]:
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
        request = search.PlaylistRequest(
            tools=body.tools,
            pain=body.pain,
            attended_talk_ids=body.attended_talk_ids,
            filter=body.filter,
            budget_minutes=body.budget_minutes,
            rerank=body.rerank,
        )
        try:
            result = search.build_playlist(client, request, model)
        except SearchBackendError as e:
            raise _backend_error(e) from e
        span.set_attribute("debrief.clips", len(result["clips"]))
        span.set_attribute("debrief.total_seconds", result["total_seconds"])
        return {
            "total_seconds": result["total_seconds"],
            "clips": result["clips"],
            "trace_id": current_trace_id(),
            "message": result["message"],
            "debug": result["debug"],
        }


@router.get("/tools")
def tools(client: Client) -> list[dict[str, Any]]:
    with tracer.start_as_current_span("api.tools") as span:
        try:
            rows = search.list_tools(client)
        except SearchBackendError as e:
            raise _backend_error(e) from e
        span.set_attribute("debrief.tools_count", len(rows))
        return rows


@router.get("/talks")
def talks(client: Client) -> list[dict[str, Any]]:
    with tracer.start_as_current_span("api.talks") as span:
        try:
            rows = search.list_talks(client)
        except SearchBackendError as e:
            raise _backend_error(e) from e
        span.set_attribute("debrief.talks_count", len(rows))
        return rows


@router.get("/clips/{clip_or_chunk_id}")
def clip(clip_or_chunk_id: str, client: Client) -> dict[str, Any]:
    with tracer.start_as_current_span("api.clip") as span:
        span.set_attribute("debrief.clip_id", clip_or_chunk_id[:128])
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
