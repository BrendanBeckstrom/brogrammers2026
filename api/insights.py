"""Query log and organizer insights: which tools teams asked about, and which no talk covered.

Every /api/playlist answer becomes one small document in the `queries` index, written in the
background (`log_query`): tool ids, flags and counts, never the pain sentence. `insights`
reads it back with `terms` aggregations, plus the count of saved playlists.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from api import search
from api.store import PLAYLISTS_INDEX, QUERIES_INDEX, StoreClient

TOP_N = 10
MAX_LOGGED_TOOLS = 50
# Only slug-shaped ids are logged; anything else is user input we have no reason to keep.
_TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

tracer = trace.get_tracer("debrief")
log = logging.getLogger("debrief.insights")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def loggable_tools(tools: Sequence[str]) -> list[str]:
    ids = dict.fromkeys(t.strip().lower() for t in tools if isinstance(t, str))
    return [t for t in ids if _TOOL_ID.match(t)][:MAX_LOGGED_TOOLS]


def query_doc(
    tools: Sequence[str],
    pain: str | None,
    filter_: str,
    rerank: bool,
    answer: Mapping[str, Any],
    duration_ms: float,
) -> dict[str, Any]:
    """The `queries` document for one playlist answer. Holds `has_pain`, never the pain text."""
    clips = answer.get("clips") or []
    debug = answer.get("debug") or {}
    logged = loggable_tools(tools)
    uncovered: list[str] = []
    if filter_ == "all":
        reported = debug.get("uncovered_tools")
        if isinstance(reported, list):
            uncovered = [t for t in logged if t in set(reported)]
        elif not clips:  # an older cached answer: an empty "all" playlist covers nothing
            uncovered = list(logged)
    return {
        "ts": now_iso(),
        "tools": logged,
        "uncovered_tools": uncovered,
        "has_pain": bool((pain or "").strip()),
        "filter": filter_,
        "rerank": bool(rerank),
        "clip_count": len(clips),
        "empty": not clips,
        "duration_ms": round(float(duration_ms), 1),
    }


def log_query(store: StoreClient | None, doc: dict[str, Any]) -> bool:
    """Write one query-log document. Never raises; returns whether it was stored."""
    if store is None:
        return False
    with tracer.start_as_current_span("querylog.write") as span:
        span.set_attributes(
            {
                "debrief.tools": doc.get("tools") or [],
                "debrief.clips": int(doc.get("clip_count") or 0),
                "debrief.empty": bool(doc.get("empty")),
            }
        )
        started = time.perf_counter()
        try:
            store.put(QUERIES_INDEX, None, doc)
        except Exception as e:  # the log must never break or slow a playlist
            span.set_attributes({"debrief.stored": False, "error.type": type(e).__name__})
            log.warning("query log write failed", extra={"error.type": type(e).__name__})
            return False
        span.set_attributes(
            {"debrief.stored": True, "debrief.duration_ms": (time.perf_counter() - started) * 1000}
        )
        return True


def insights_body() -> dict[str, Any]:
    return {
        "size": 0,
        "track_total_hits": True,
        "aggs": {
            "top_tools": {"terms": {"field": "tools", "size": TOP_N}},
            "empty_tools": {"terms": {"field": "uncovered_tools", "size": TOP_N}},
        },
    }


def _rows(buckets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": str(b["key"]), "name": search.tool_name(str(b["key"])), "count": int(b["doc_count"])}
        for b in buckets
    ]


def _total(response: Mapping[str, Any]) -> int:
    total = response.get("hits", {}).get("total", 0)
    return int(total.get("value", 0) if isinstance(total, Mapping) else total or 0)


def insights(store: StoreClient) -> dict[str, Any]:
    """`{top_tools, empty_tools, requests, playlists_saved}`; missing indexes count as empty.

    Raises SearchBackendError when Elastic fails.
    """
    saved_f = search.submit(lambda: store.count(PLAYLISTS_INDEX))
    response = store.search(QUERIES_INDEX, insights_body())
    aggs = response.get("aggregations") or {}
    return {
        "top_tools": _rows(aggs.get("top_tools", {}).get("buckets", [])),
        "empty_tools": _rows(aggs.get("empty_tools", {}).get("buckets", [])),
        "requests": _total(response),
        "playlists_saved": int(saved_f.result()),
    }
