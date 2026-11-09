"""Saved, shareable playlists in the `playlists` index (docs/BRIEF.md, API).

The browser sends only the request and the clip ids it shows. The answer saved is the
server's own: from `recent` (answers this process gave lately), else the demo cache, else a
fresh run, and every clip id must be one of its clips. Never trusts clip text from the
browser.
"""

from __future__ import annotations

import re
import secrets
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any

from api.insights import now_iso
from api.store import PLAYLISTS_INDEX, DocumentExists, StoreClient

ID_BYTES = 6  # 8 URL-safe characters
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
RECENT_MAX = 256


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
