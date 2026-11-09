"""Debrief's own Elastic indexes (docs/BRIEF.md, API): saved `playlists`, the `queries` log
and recent playlist `answers` shared by every replica.

The `clips` and `talks` indexes are read-only here. These three are written by the API and
created on first use with an explicit mapping. Elastic sits behind `StoreClient`, so unit
tests run against a fake. Writes use a short timeout and no retries: the query log and the
shared answers are written in the background and must never hold a worker for long.
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from typing import Any, Protocol

from api.search import SearchBackendError, _is_unreachable

PLAYLISTS_INDEX = "playlists"
QUERIES_INDEX = "queries"
ANSWERS_INDEX = "answers"
REQUEST_TIMEOUT_SEC = 5.0

PLAYLISTS_MAPPINGS: dict[str, Any] = {
    "dynamic": False,
    "properties": {
        "id": {"type": "keyword"},
        "created_at": {"type": "date"},
        "clip_ids": {"type": "keyword"},
        "request": {
            "dynamic": False,
            "properties": {
                "tools": {"type": "keyword"},
                "filter": {"type": "keyword"},
                "rerank": {"type": "boolean"},
                "budget_minutes": {"type": "integer"},
            },
        },
        # The whole playlist answer, kept for GET /api/playlists/{id}; never searched.
        "response": {"type": "object", "enabled": False},
    },
}

QUERIES_MAPPINGS: dict[str, Any] = {
    # Strict: a field that is not listed here (say, the pain sentence) is refused.
    "dynamic": "strict",
    "properties": {
        "ts": {"type": "date"},
        "tools": {"type": "keyword"},
        "uncovered_tools": {"type": "keyword"},
        "has_pain": {"type": "boolean"},
        "filter": {"type": "keyword"},
        "rerank": {"type": "boolean"},
        "clip_count": {"type": "integer"},
        "empty": {"type": "boolean"},
        "duration_ms": {"type": "float"},
    },
}

ANSWERS_MAPPINGS: dict[str, Any] = {
    "dynamic": False,
    "properties": {
        "request_hash": {"type": "keyword"},
        "clip_ids": {"type": "keyword"},
        "ts": {"type": "date"},
        # The playlist answer as the browser saw it; only POST /api/playlists reads it back.
        "response": {"type": "object", "enabled": False},
    },
}

MAPPINGS: dict[str, dict[str, Any]] = {
    PLAYLISTS_INDEX: PLAYLISTS_MAPPINGS,
    QUERIES_INDEX: QUERIES_MAPPINGS,
    ANSWERS_INDEX: ANSWERS_MAPPINGS,
}

log = logging.getLogger("debrief.store")


class DocumentExists(SearchBackendError):
    """`put(..., create=True)` found a document with that id already."""


class StoreClient(Protocol):
    """The Elastic calls for Debrief's own indexes; `FakeStore` in the tests implements it."""

    def ensure_index(self, index: str) -> None: ...

    def put(
        self, index: str, doc_id: str | None, doc: dict[str, Any], create: bool = False
    ) -> None:
        """Index one document; with `create`, raise DocumentExists if the id is taken."""
        ...

    def get(self, index: str, doc_id: str) -> dict[str, Any] | None:
        """The document's `_source`, or None (also when the index does not exist)."""
        ...

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        """A search; a missing index answers as an empty result."""
        ...

    def count(self, index: str) -> int:
        """Documents in the index; 0 when it does not exist."""
        ...


def _error_type(e: Exception) -> str:
    return str(getattr(e, "error", "") or "")


class ElasticStoreClient:
    """`StoreClient` over the official client. Errors become SearchBackendError."""

    def __init__(self, url: str, api_key: str) -> None:
        from elasticsearch import Elasticsearch

        self._es = Elasticsearch(
            url, api_key=api_key, request_timeout=REQUEST_TIMEOUT_SEC, max_retries=0
        )
        self._ready: set[str] = set()
        self._lock = threading.Lock()

    def ensure_index(self, index: str) -> None:
        if index in self._ready:
            return
        from elasticsearch import BadRequestError

        with self._lock:
            if index in self._ready:
                return
            try:
                self._es.indices.create(index=index, mappings=MAPPINGS[index])
                log.info("created index", extra={"debrief.index": index})
            except BadRequestError as e:
                if _error_type(e) != "resource_already_exists_exception":
                    raise SearchBackendError(
                        f"Elastic create index failed ({type(e).__name__})"
                    ) from e
            except Exception as e:
                raise SearchBackendError(
                    f"Elastic create index failed ({type(e).__name__})",
                    unreachable=_is_unreachable(e),
                ) from e
            self._ready.add(index)

    def put(
        self, index: str, doc_id: str | None, doc: dict[str, Any], create: bool = False
    ) -> None:
        from elasticsearch import ConflictError

        self.ensure_index(index)
        try:
            self._es.index(
                index=index, id=doc_id, document=doc, op_type="create" if create else None
            )
        except ConflictError as e:
            raise DocumentExists(f"document {doc_id} exists") from e
        except Exception as e:
            raise SearchBackendError(
                f"Elastic index failed ({type(e).__name__})", unreachable=_is_unreachable(e)
            ) from e

    def get(self, index: str, doc_id: str) -> dict[str, Any] | None:
        from elasticsearch import NotFoundError

        try:
            doc = self._es.get(index=index, id=doc_id)
        except NotFoundError:
            return None  # no such document, or no index yet
        except Exception as e:
            raise SearchBackendError(
                f"Elastic get failed ({type(e).__name__})", unreachable=_is_unreachable(e)
            ) from e
        return dict(doc.get("_source") or {})

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        from elasticsearch import NotFoundError

        try:
            return dict(self._es.search(index=index, body=body).body)
        except NotFoundError:
            return {}
        except Exception as e:
            raise SearchBackendError(
                f"Elastic search failed ({type(e).__name__})", unreachable=_is_unreachable(e)
            ) from e

    def count(self, index: str) -> int:
        from elasticsearch import NotFoundError

        try:
            return int(self._es.count(index=index)["count"])
        except NotFoundError:
            return 0
        except Exception as e:
            raise SearchBackendError(
                f"Elastic count failed ({type(e).__name__})", unreachable=_is_unreachable(e)
            ) from e


@lru_cache(maxsize=1)
def get_store() -> StoreClient:
    """The process-wide store client, from ELASTIC_URL/ELASTIC_API_KEY."""
    url = os.environ.get("ELASTIC_URL", "").strip()
    api_key = os.environ.get("ELASTIC_API_KEY", "").strip()
    if not url or not api_key:
        raise SearchBackendError("ELASTIC_URL and ELASTIC_API_KEY must be set")
    return ElasticStoreClient(url, api_key)


def warm() -> None:
    """Create Debrief's own indexes in a background thread at startup. Never raises.

    Creating an index takes over a second on serverless; done lazily, the first shared
    answer after a deploy could land after the user already pressed Save on another pod.
    """

    def create() -> None:
        try:
            client = get_store()
            for index in MAPPINGS:
                client.ensure_index(index)
        except Exception as e:  # not configured, or Elastic down: the lazy path still works
            log.warning("index warm-up skipped", extra={"error.type": type(e).__name__})

    threading.Thread(target=create, name="store-warm", daemon=True).start()


class OfflineStore:
    """Stands in for Elastic when the demo cache is on but Elastic is not configured."""

    def ensure_index(self, index: str) -> None:
        raise SearchBackendError("Elastic is not configured")

    def put(
        self, index: str, doc_id: str | None, doc: dict[str, Any], create: bool = False
    ) -> None:
        raise SearchBackendError("Elastic is not configured")

    def get(self, index: str, doc_id: str) -> dict[str, Any] | None:
        raise SearchBackendError("Elastic is not configured")

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        raise SearchBackendError("Elastic is not configured")

    def count(self, index: str) -> int:
        raise SearchBackendError("Elastic is not configured")
