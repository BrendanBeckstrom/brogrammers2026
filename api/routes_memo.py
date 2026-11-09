"""POST /api/memo (task 9): a cited memo for the clips the team picked.

The request carries only clip ids. Their text is loaded from the index by id (never
taken from the browser), unknown ids are a 422, and a model failure is a 502: there is
no fake memo. The span carries counts and token totals only. With DEBRIEF_CACHE=on a cached
memo (api/demo_cache.py, task 13) is returned without touching Elastic or the model.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Response
from opentelemetry import trace
from pydantic import BaseModel, Field

from api import demo_cache, search
from api import memo as memomod
from api.llm import LLMError
from api.routes_search import Client, Model
from api.search import SearchBackendError

router = APIRouter(prefix="/api")
tracer = trace.get_tracer("debrief")

ClipId = Annotated[str, Field(min_length=1, max_length=200)]


class MemoBody(BaseModel):
    tools: list[str] = Field(default_factory=list, max_length=50)
    clip_ids: list[ClipId] = Field(min_length=1, max_length=memomod.MAX_CLIP_IDS)


@router.post("/memo")
def memo(body: MemoBody, client: Client, model: Model, response: Response) -> dict[str, Any]:
    with tracer.start_as_current_span("api.memo") as span:
        clip_ids = list(dict.fromkeys(body.clip_ids))
        tool_ids = list(dict.fromkeys(t.strip() for t in body.tools if t.strip()))
        span.set_attributes({"debrief.tools": tool_ids, "debrief.clips_requested": len(clip_ids)})
        cached = demo_cache.serve("memo", body.model_dump(mode="json"), span, response)
        if cached is not None:
            return cached
        try:
            found, unknown = search.resolve_clips(client, clip_ids)
        except SearchBackendError as e:
            if demo_cache.enabled():
                raise demo_cache.miss_error(str(e)) from e
            raise HTTPException(status_code=502, detail=str(e)) from e
        if unknown:
            span.set_attribute("debrief.unknown_clip_ids", len(unknown))
            shown = ", ".join(unknown[:5]) + (" …" if len(unknown) > 5 else "")
            raise HTTPException(status_code=422, detail=f"Unknown clip ids: {shown}")
        tool_names = [search.tool_name(t) for t in tool_ids if t in search.tool_catalog()]
        try:
            result, stats = memomod.write_memo(model, tool_names, [found[i] for i in clip_ids])
        except (LLMError, memomod.MemoError) as e:
            span.set_attribute("error.type", type(e).__name__)
            if isinstance(e, LLMError) and demo_cache.enabled():
                raise demo_cache.miss_error("the memo model did not answer") from e
            detail = (
                str(e)
                if isinstance(e, memomod.MemoError)
                else "The memo model is unavailable right now; try again in a minute."
            )
            raise HTTPException(status_code=502, detail=detail) from e
        span.set_attributes({f"debrief.memo.{k}": v for k, v in stats.items()})
        return result
