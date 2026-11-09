"""Saved playlists and organizer insights: POST /api/playlists, GET /api/playlists/{id},
GET /api/insights (docs/BRIEF.md, API).

Each route runs inside its own OpenTelemetry span (ids and counts only, never the pain
sentence or transcript text) and gets Debrief's own indexes through `playlist_store`, which
tests override with a fake. Saving always goes to Elastic; only /api/insights is served
from the demo cache. A save finds the answer the browser saw in this process's memory, then
in the `answers` index any replica wrote (api/playlists.py), then the demo cache, and only
then runs the playlist again.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from opentelemetry import trace
from pydantic import BaseModel, Field, StringConstraints

from api import demo_cache, insights, playlists, store
from api.routes_search import Client, Model, PlaylistBody, _backend_error, run_playlist
from api.search import SearchBackendError
from api.store import StoreClient

MAX_BODY_BYTES = 32 * 1024
MAX_CLIP_IDS = 25

router = APIRouter(prefix="/api")
tracer = trace.get_tracer("debrief")
log = logging.getLogger("debrief.api")

ClipId = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class SavePlaylistBody(BaseModel):
    request: PlaylistBody
    clip_ids: list[ClipId] = Field(min_length=1, max_length=MAX_CLIP_IDS)


def playlist_store() -> StoreClient:
    """Debrief's own indexes; a FastAPI dependency so tests can swap in a fake."""
    try:
        return store.get_store()
    except SearchBackendError as e:
        if demo_cache.enabled():
            return store.OfflineStore()  # cached /api/insights still answers; the rest 503
        raise HTTPException(status_code=503, detail="Search is not configured") from e


Store = Annotated[StoreClient, Depends(playlist_store)]


async def capped_body(request: Request) -> None:
    """413 for a request body over MAX_BODY_BYTES (checked before it is parsed)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"Body over {MAX_BODY_BYTES} bytes")
    if len(await request.body()) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"Body over {MAX_BODY_BYTES} bytes")


def _store_error(e: SearchBackendError, saved: StoreClient) -> HTTPException:
    """503 (offline demo) when the cache is on and Elastic is unreachable, else 502."""
    offline = isinstance(saved, store.OfflineStore) or e.unreachable
    if demo_cache.enabled() and offline:
        return demo_cache.miss_error(str(e))
    return HTTPException(status_code=502, detail=str(e))


@router.post("/playlists", status_code=201, dependencies=[Depends(capped_body)])
def save_playlist(
    body: SavePlaylistBody, client: Client, model: Model, saved: Store
) -> dict[str, str]:
    with tracer.start_as_current_span("api.playlists.save") as span:
        request = body.request.model_dump(mode="json")
        span.set_attributes(
            {
                "debrief.tools": body.request.tools,
                "debrief.filter": body.request.filter,
                "debrief.rerank_requested": body.request.rerank,
                "debrief.clip_ids_count": len(body.clip_ids),
            }
        )
        key = demo_cache.cache_key("playlist", request)
        answer = playlists.recent.get(key)
        source = "recent"
        if answer is not None and not playlists.covers(answer, body.clip_ids):
            answer = None  # this process gave a different answer; another replica may have it
        if answer is None:
            answer, how = playlists.wait_for_shared(saved, key, body.clip_ids)
            source = f"answers:{how}"
        if answer is None and demo_cache.enabled():
            answer = demo_cache.lookup("playlist", request)
            source = "cache"
        if answer is None:
            source = "rerun"
            try:
                answer = run_playlist(body.request, client, model)
            except SearchBackendError as e:
                raise _backend_error(e, client) from e
        answer = {"terms": [], **answer}  # a cached answer from before significant terms
        span.set_attribute("debrief.playlist_source", source)
        log.info("playlist save", extra={"debrief.playlist_source": source})
        try:
            chosen = playlists.shareable(playlists.pick_clips(answer, body.clip_ids))
        except playlists.UnknownClips as e:
            span.set_attribute("debrief.unknown_clips", len(e.ids))
            raise HTTPException(
                status_code=422,
                detail=f"{len(e.ids)} of the clip ids are not in this playlist; "
                "run the playlist again and save that one.",
            ) from e
        stored_request = {k: v for k, v in request.items() if k != "pain"}
        try:
            playlist_id = playlists.save(saved, stored_request, body.clip_ids, chosen)
        except SearchBackendError as e:
            raise _store_error(e, saved) from e
        span.set_attributes(
            {"debrief.playlist_id": playlist_id, "debrief.clips": len(chosen["clips"])}
        )
        return {"id": playlist_id, "url": f"/?p={playlist_id}"}


@router.get("/playlists/{playlist_id}")
def get_playlist(playlist_id: str, saved: Store) -> dict[str, Any]:
    with tracer.start_as_current_span("api.playlists.get") as span:
        span.set_attribute("debrief.playlist_id", playlist_id[:64])
        try:
            found = playlists.load(saved, playlist_id)
        except SearchBackendError as e:
            raise _store_error(e, saved) from e
        span.set_attribute("debrief.found", found is not None)
        if found is None:
            raise HTTPException(status_code=404, detail="Unknown playlist id")
        span.set_attribute("debrief.clips", len(found["response"].get("clips") or []))
        return found


@router.get("/insights")
def get_insights(saved: Store, response: Response) -> dict[str, Any]:
    with tracer.start_as_current_span("api.insights") as span:
        cached = demo_cache.serve("insights", None, span, response)
        if cached is None:
            try:
                cached = insights.insights(saved)
            except SearchBackendError as e:
                raise _store_error(e, saved) from e
        span.set_attributes(
            {
                "debrief.requests": int(cached.get("requests") or 0),
                "debrief.playlists_saved": int(cached.get("playlists_saved") or 0),
                "debrief.top_tools": len(cached.get("top_tools") or []),
                "debrief.empty_tools": len(cached.get("empty_tools") or []),
            }
        )
        return cached
