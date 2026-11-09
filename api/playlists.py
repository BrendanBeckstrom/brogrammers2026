"""Saved, shareable playlists in the `playlists` index (docs/BRIEF.md, API).

The browser sends only the request and the clip ids it shows. The answer saved is the
server's own, looked up in this order: `recent` (answers this process gave lately), the
`answers` index (answers any replica gave lately, see `share_answer`), the demo cache, and
only then a fresh run. Every clip id must be one of its clips. Never trusts clip text from
the browser.

The API runs more than one replica, so a save can land on a pod that never saw the answer.
Each /api/playlist answer is therefore also written to `answers` in the background, twice:
under `<request hash>:<clip-set hash>` (the exact answer, found by GET when the browser
saves every clip it was shown) and under `<request hash>` (the latest answer for that
request, for a save of a subset). A search on `request_hash` plus every clip id covers an
older answer once Elastic has refreshed.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry import trace

from api.insights import now_iso
from api.store import ANSWERS_INDEX, PLAYLISTS_INDEX, DocumentExists, StoreClient

ID_BYTES = 6  # 8 URL-safe characters
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
RECENT_MAX = 256
# On a miss in `answers`, look again after these pauses before running the playlist again:
# the replica that answered writes it just after its response.
SHARED_RETRY_DELAYS: tuple[float, ...] = (0.3, 0.7)

tracer = trace.get_tracer("debrief")
log = logging.getLogger("debrief.playlists")


class UnknownClips(ValueError):
    """Clip ids that are not in the playlist for this request."""

    def __init__(self, ids: Sequence[str]) -> None:
        super().__init__(f"{len(ids)} clip ids are not in this playlist")
        self.ids = list(ids)


class RecentAnswers:
    """The last RECENT_MAX playlist answers by cache key, so a save can reuse the exact one."""

    def __init__(self, size: int = RECENT_MAX) -> None:
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def put(self, key: str, answer: Mapping[str, Any]) -> None:
        with self._lock:
            self._items[key] = dict(answer)
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            answer = self._items.get(key)
            return dict(answer) if answer is not None else None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


recent = RecentAnswers()


def new_id() -> str:
    return secrets.token_urlsafe(ID_BYTES)


def valid_id(playlist_id: str) -> bool:
    return bool(ID_PATTERN.match(playlist_id))


def covers(answer: Mapping[str, Any], clip_ids: Sequence[str]) -> bool:
    """True if every clip id is a clip of the answer."""
    return set(clip_ids) <= {c["clip_id"] for c in answer.get("clips") or []}


def pick_clips(answer: Mapping[str, Any], clip_ids: Sequence[str]) -> dict[str, Any]:
    """The answer cut to `clip_ids`, in that order, with `total_seconds` recomputed.

    Raises UnknownClips if any id is not a clip of the answer.
    """
    by_id = {c["clip_id"]: c for c in answer.get("clips") or []}
    wanted = list(dict.fromkeys(clip_ids))
    unknown = [c for c in wanted if c not in by_id]
    if unknown:
        raise UnknownClips(unknown)
    clips = [by_id[c] for c in wanted]
    return {
        **answer,
        "clips": clips,
        "total_seconds": sum(c["end_sec"] - c["start_sec"] for c in clips),
    }


def save(
    store: StoreClient,
    request: Mapping[str, Any],
    clip_ids: Sequence[str],
    response: Mapping[str, Any],
) -> str:
    """Store one playlist under a fresh random id and return the id.

    Raises SearchBackendError when Elastic fails.
    """
    for attempt in range(3):
        playlist_id = new_id()
        doc = {
            "id": playlist_id,
            "request": dict(request),
            "clip_ids": list(clip_ids),
            "response": dict(response),
            "created_at": now_iso(),
        }
        try:
            store.put(PLAYLISTS_INDEX, playlist_id, doc, create=True)
        except DocumentExists:
            if attempt == 2:
                raise
            continue
        return playlist_id
    raise AssertionError("unreachable")


def load(store: StoreClient, playlist_id: str) -> dict[str, Any] | None:
    """`{request, response, created_at}` for a saved playlist, or None."""
    if not valid_id(playlist_id):
        return None
    doc = store.get(PLAYLISTS_INDEX, playlist_id)
    if not doc:
        return None
    return {
        "request": doc.get("request") or {},
        "response": doc.get("response") or {},
        "created_at": doc.get("created_at"),
    }


# --- Answers shared across replicas -------------------------------------------------------


def clip_set_hash(clip_ids: Sequence[str]) -> str:
    """Order-free hash of a set of clip ids."""
    joined = "\n".join(sorted(set(clip_ids)))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def exact_answer_id(request_hash: str, clip_ids: Sequence[str]) -> str:
    return f"{request_hash}:{clip_set_hash(clip_ids)}"


def _clip_ids(answer: Mapping[str, Any]) -> list[str]:
    return [str(c["clip_id"]) for c in answer.get("clips") or []]


def shareable(answer: Mapping[str, Any]) -> dict[str, Any]:
    """The answer as stored in `answers`: `debug.query` dropped, since it holds the pain."""
    debug = answer.get("debug")
    if not isinstance(debug, Mapping):
        return dict(answer)
    return {**answer, "debug": {k: v for k, v in debug.items() if k != "query"}}


def share_answer(store: StoreClient | None, request_hash: str, answer: Mapping[str, Any]) -> bool:
    """Write one answer to `answers` for every replica. Never raises; True if stored."""
    clip_ids = _clip_ids(answer)
    if store is None or not clip_ids:
        return False  # an empty playlist has nothing to save
    with tracer.start_as_current_span("answers.write") as span:
        span.set_attribute("debrief.clips", len(clip_ids))
        started = time.perf_counter()
        doc = {
            "request_hash": request_hash,
            "clip_ids": clip_ids,
            "response": shareable(answer),
            "ts": now_iso(),
        }
        try:
            store.put(ANSWERS_INDEX, exact_answer_id(request_hash, clip_ids), doc)
            store.put(ANSWERS_INDEX, request_hash, doc)
        except Exception as e:  # never break or slow a playlist
            span.set_attributes({"debrief.stored": False, "error.type": type(e).__name__})
            log.warning("shared answer write failed", extra={"error.type": type(e).__name__})
            return False
        span.set_attributes(
            {"debrief.stored": True, "debrief.duration_ms": (time.perf_counter() - started) * 1000}
        )
        return True


def _covers(doc: Mapping[str, Any] | None, request_hash: str, wanted: set[str]) -> bool:
    if not doc or doc.get("request_hash") != request_hash:
        return False
    return wanted <= set(_clip_ids(doc.get("response") or {}))


def wait_for_shared(
    store: StoreClient | None, request_hash: str, clip_ids: Sequence[str]
) -> tuple[dict[str, Any] | None, str]:
    """`find_shared`, looked up again after each SHARED_RETRY_DELAYS pause while it misses."""
    answer, how = find_shared(store, request_hash, clip_ids)
    for delay in SHARED_RETRY_DELAYS if store is not None else ():
        if answer is not None or how == "error":
            break
        time.sleep(delay)
        answer, how = find_shared(store, request_hash, clip_ids)
    return answer, how


def find_shared(
    store: StoreClient | None, request_hash: str, clip_ids: Sequence[str]
) -> tuple[dict[str, Any] | None, str]:
    """(an answer any replica gave for this request that holds every clip id, how found).

    Tries the exact answer, then the latest one, then a search. Never raises: a failure
    means the save falls through to the demo cache or a fresh run.
    """
    if store is None:
        return None, "none"
    wanted = set(clip_ids)
    try:
        for doc_id, how in (
            (exact_answer_id(request_hash, clip_ids), "exact"),
            (request_hash, "latest"),
        ):
            doc = store.get(ANSWERS_INDEX, doc_id)
            if _covers(doc, request_hash, wanted):
                return dict(doc["response"]), how
        filters: list[dict[str, Any]] = [{"term": {"request_hash": request_hash}}]
        filters += [{"term": {"clip_ids": c}} for c in sorted(wanted)]
        body = {
            "size": 1,
            "query": {"bool": {"filter": filters}},
            "sort": [{"ts": "desc"}],
        }
        hits = store.search(ANSWERS_INDEX, body).get("hits", {}).get("hits", [])
        if hits and _covers(hits[0].get("_source"), request_hash, wanted):
            return dict(hits[0]["_source"]["response"]), "search"
    except Exception as e:
        log.warning("shared answer lookup failed", extra={"error.type": type(e).__name__})
        return None, "error"
    return None, "miss"
