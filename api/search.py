"""Hybrid search over the `clips` index and the playlist built from it (task 6).

One RRF request (keyword + semantic) ranks the chunks; the two halves also run on their
own as small `_id`-only queries, in parallel, so every clip says which half found it.
Elastic sits behind `SearchClient`, so unit tests run against a fake.

Spans carry ids, counts, scores and timings only, never transcript text or the pain
sentence.
"""

from __future__ import annotations

import contextvars
import os
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from opentelemetry import trace

from api import clips as clipmod
from api.clips import Hit
from ingest.tag import Tool, load_tools

CLIPS_INDEX = "clips"
TALKS_INDEX = "talks"
WINDOW = 100  # hits per half and per RRF request (rank_window_size)
MIN_TOOL_CHUNKS = 5  # /api/tools lists only tools with at least this many chunks
REQUEST_TIMEOUT_SEC = 10.0
SOURCE_FIELDS = [
    "chunk_id",
    "talk_id",
    "youtube_id",
    "title",
    "speakers",
    "start_sec",
    "end_sec",
    "text",
    "tools",
    "talk_label",
]
TOOLS_PATH = Path(__file__).resolve().parent.parent / "data" / "tools.yaml"

Filter = Literal["all", "attended", "missed"]

tracer = trace.get_tracer("debrief")
_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="search")


class SearchBackendError(RuntimeError):
    """Elastic failed or is not configured. The message never holds secrets."""


class SearchClient(Protocol):
    """The two Elastic calls Debrief needs; `FakeSearchClient` in the tests implements it."""

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def mget(self, index: str, ids: Sequence[str]) -> list[dict[str, Any]]: ...


class ElasticSearchClient:
    """`SearchClient` over the official client. Errors become SearchBackendError."""

    def __init__(self, url: str, api_key: str) -> None:
        from elasticsearch import Elasticsearch

        self._es = Elasticsearch(
            url, api_key=api_key, request_timeout=REQUEST_TIMEOUT_SEC, max_retries=2
        )

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return dict(self._es.search(index=index, body=body).body)
        except Exception as e:  # ApiError, TransportError: keep the type, drop details
            raise SearchBackendError(f"Elastic search failed ({type(e).__name__})") from e

    def mget(self, index: str, ids: Sequence[str]) -> list[dict[str, Any]]:
        try:
            docs = self._es.mget(index=index, ids=list(ids), source_includes=SOURCE_FIELDS)
        except Exception as e:
            raise SearchBackendError(f"Elastic mget failed ({type(e).__name__})") from e
        return [d["_source"] for d in docs["docs"] if d.get("found")]


@lru_cache(maxsize=1)
def get_client() -> SearchClient:
    """The process-wide Elastic client, created on first use from ELASTIC_URL/ELASTIC_API_KEY."""
    url = os.environ.get("ELASTIC_URL", "").strip()
    api_key = os.environ.get("ELASTIC_API_KEY", "").strip()
    if not url or not api_key:
        raise SearchBackendError("ELASTIC_URL and ELASTIC_API_KEY must be set")
    return ElasticSearchClient(url, api_key)


@lru_cache(maxsize=1)
def tool_catalog() -> dict[str, Tool]:
    """data/tools.yaml by id (DEBRIEF_TOOLS overrides the path)."""
    path = os.environ.get("DEBRIEF_TOOLS") or TOOLS_PATH
    return {t.id: t for t in load_tools(path)}


def tool_name(tool_id: str) -> str:
    tool = tool_catalog().get(tool_id)
    return tool.name if tool else tool_id


def tool_terms(tool_ids: Sequence[str]) -> list[str]:
    """Names, aliases and caption spellings of the tools, lowercased and de-duplicated."""
    seen: dict[str, None] = {}
    for tool_id in tool_ids:
        tool = tool_catalog().get(tool_id)
        if tool is None:
            continue
        for term in (tool.name, *tool.terms):
            seen.setdefault(term.strip().lower(), None)
    return [t for t in seen if t]


# --- Query bodies ---------------------------------------------------------------------


def text_query(terms: Sequence[str]) -> dict[str, Any]:
    """Phrase matches on `text`; a plain `match` would split "argo cd" into a bare "argo"."""
    return {
        "bool": {
            "should": [{"match_phrase": {"text": term}} for term in terms],
            "minimum_should_match": 1,
        }
    }


def keyword_query(tool_ids: Sequence[str], terms: Sequence[str]) -> dict[str, Any]:
    should: list[dict[str, Any]] = [{"terms": {"tools": list(tool_ids), "boost": 2}}]
    should += [{"match_phrase": {"text": term}} for term in terms]
    return {"bool": {"should": should, "minimum_should_match": 1}}


def semantic_query(text: str) -> dict[str, Any]:
    return {"semantic": {"field": "text_semantic", "query": text}}


def talk_filter(filter_: Filter, attended_talk_ids: Sequence[str]) -> dict[str, Any] | None:
    if filter_ == "attended":
        return {"terms": {"talk_id": list(attended_talk_ids)}}
    if filter_ == "missed" and attended_talk_ids:
        return {"bool": {"must_not": [{"terms": {"talk_id": list(attended_talk_ids)}}]}}
    return None


def standard(query: dict[str, Any], filter_: dict[str, Any] | None) -> dict[str, Any]:
    retriever: dict[str, Any] = {"query": query}
    if filter_ is not None:
        retriever["filter"] = filter_
    return {"standard": retriever}


def rrf_body(
    keyword: dict[str, Any],
    semantic: dict[str, Any],
    filter_: dict[str, Any] | None,
    highlight_terms: Sequence[str],
) -> dict[str, Any]:
    """The hybrid request: RRF over the keyword and semantic retrievers, plus a highlight."""
    body: dict[str, Any] = {
        "size": WINDOW,
        "_source": SOURCE_FIELDS,
        "retriever": {
            "rrf": {
                "rank_window_size": WINDOW,
                "retrievers": [standard(keyword, filter_), standard(semantic, filter_)],
            }
        },
    }
    if highlight_terms:
        body["highlight"] = {
            "fields": {
                "text": {
                    "type": "unified",
                    "boundary_scanner": "sentence",
                    "fragment_size": 200,
                    "number_of_fragments": 1,
                    "no_match_size": 0,
                    "highlight_query": text_query(highlight_terms),
                }
            }
        }
    return body


def ids_body(
    query: dict[str, Any],
    filter_: dict[str, Any] | None,
    tool_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """One half on its own, `_id`s only; the keyword half also counts chunks per tool."""
    body: dict[str, Any] = {
        "size": WINDOW,
        "_source": False,
        "track_total_hits": False,
        "retriever": standard(query, filter_),
    }
    if tool_ids:
        body["aggs"] = {
            "tools": {
                "filter": {"terms": {"tools": list(tool_ids)}},
                "aggs": {
                    "per_tool": {
                        "terms": {"field": "tools", "include": list(tool_ids), "size": 100}
                    }
                },
            }
        }
    return body


# --- Playlist -------------------------------------------------------------------------


@dataclass
class PlaylistRequest:
    tools: list[str]
    pain: str | None = None
    attended_talk_ids: list[str] = field(default_factory=list)
    filter: Filter = "all"
    budget_minutes: int = 25
    rerank: bool = False


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _submit(fn: Callable[[], Any]) -> Future[Any]:
    """Run `fn` on the pool inside a copy of the current context, so spans nest."""
    ctx = contextvars.copy_context()
    return _pool.submit(ctx.run, fn)


def _timed_search(
    client: SearchClient, span_name: str, body: dict[str, Any], attrs: Mapping[str, Any]
) -> tuple[dict[str, Any], float]:
    with tracer.start_as_current_span(span_name) as span:
        span.set_attributes(dict(attrs))
        started = time.perf_counter()
        response = client.search(CLIPS_INDEX, body)
        elapsed = _ms(started)
        span.set_attribute("debrief.hits", len(response.get("hits", {}).get("hits", [])))
        span.set_attribute("debrief.elastic_took_ms", int(response.get("took", 0)))
        span.set_attribute("debrief.duration_ms", elapsed)
        return response, elapsed


def _hit_ids(response: Mapping[str, Any]) -> list[str]:
    return [h["_id"] for h in response.get("hits", {}).get("hits", [])]


def _tool_hits(response: Mapping[str, Any]) -> dict[str, int]:
    buckets = response.get("aggregations", {}).get("tools", {}).get("per_tool", {})
    return {b["key"]: int(b["doc_count"]) for b in buckets.get("buckets", [])}


def _empty(message: str, debug: dict[str, Any]) -> dict[str, Any]:
    return {"total_seconds": 0, "clips": [], "message": message, "debug": debug}


def _names(tool_ids: Sequence[str]) -> str:
    names = [tool_name(t) for t in tool_ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def build_playlist(client: SearchClient, req: PlaylistRequest) -> dict[str, Any]:
    """Search, group by talk, build clips and fill the budget.

    Returns the playlist response without `trace_id` (the route adds it).
    """
    total_started = time.perf_counter()
    tool_ids = list(dict.fromkeys(t.strip() for t in req.tools if t.strip()))
    timings: dict[str, float] = {}
    debug: dict[str, Any] = {"query": None, "timings_ms": timings, "hits": 0, "talks": 0}
    if not tool_ids:
        return _empty("Pick at least one tool from your stack.", debug)
    if req.filter == "attended" and not req.attended_talk_ids:
        return _empty("Tick the talks your team attended to use this filter.", debug)

    terms = tool_terms(tool_ids)
    filter_ = talk_filter(req.filter, req.attended_talk_ids)
    pain = (req.pain or "").strip()
    keyword = keyword_query(tool_ids, terms)
    semantic = semantic_query(pain or ", ".join(tool_name(t) for t in tool_ids))
    body = rrf_body(keyword, semantic, filter_, terms)
    debug["query"] = body
    attrs = {"debrief.tools": tool_ids, "debrief.filter": req.filter}

    search_started = time.perf_counter()
    hybrid_f = _submit(lambda: _timed_search(client, "search.hybrid", body, attrs))
    keyword_f = _submit(
        lambda: _timed_search(client, "search.keyword", ids_body(keyword, filter_, tool_ids), attrs)
    )
    semantic_f = _submit(
        lambda: _timed_search(client, "search.semantic", ids_body(semantic, filter_), attrs)
    )
    hybrid, timings["hybrid_search"] = hybrid_f.result()
    keyword_resp, timings["keyword_search"] = keyword_f.result()
    semantic_resp, timings["semantic_search"] = semantic_f.result()
    timings["search_wall"] = _ms(search_started)

    covered = _tool_hits(keyword_resp)
    uncovered = [t for t in tool_ids if not covered.get(t)]
    if len(uncovered) == len(tool_ids):
        where = {"all": "", "attended": " you attended", "missed": " you missed"}[req.filter]
        timings["total"] = _ms(total_started)
        return _empty(f"No talk{where} mentions {_names(tool_ids)}.", debug)

    with tracer.start_as_current_span("search.group") as span:
        started = time.perf_counter()
        keyword_ids = set(_hit_ids(keyword_resp))
        semantic_ids = set(_hit_ids(semantic_resp))
        attended = set(req.attended_talk_ids)
        hits: list[Hit] = []
        for raw in hybrid.get("hits", {}).get("hits", []):
            source = raw.get("_source") or {}
            if req.filter == "missed" and source.get("talk_id") in attended:
                continue
            if req.filter == "attended" and source.get("talk_id") not in attended:
                continue
            in_kw, in_sem = raw["_id"] in keyword_ids, raw["_id"] in semantic_ids
            if in_kw and in_sem:
                matched_by = "both"
            elif in_sem or (not in_kw and not set(source.get("tools") or ()) & set(tool_ids)):
                matched_by = "semantic"
            else:
                matched_by = "keyword"
            fragments = (raw.get("highlight") or {}).get("text") or [None]
            hits.append(Hit.from_source(source, raw.get("_score") or 0.0, matched_by, fragments[0]))
        groups = clipmod.group_by_talk(hits)
        timings["grouping"] = _ms(started)
        span.set_attributes(
            {
                "debrief.hits": len(hits),
                "debrief.talks": len(groups),
                "debrief.keyword_hits": len(keyword_ids),
                "debrief.semantic_hits": len(semantic_ids),
            }
        )

    with tracer.start_as_current_span("search.clips") as span:
        started = time.perf_counter()
        candidates = clipmod.build_clips(groups)
        chosen = clipmod.fill_budget(candidates, req.budget_minutes * 60, tool_ids)
        catalog = tool_catalog()
        names = {t: tool_name(t) for t in tool_ids}
        tool_term_map = {t: catalog[t].terms for t in tool_ids if t in catalog}
        records = [
            clipmod.clip_record(c, tool_ids, names, tool_term_map, pain or None) for c in chosen
        ]
        total_seconds = sum(r["end_sec"] - r["start_sec"] for r in records)
        timings["clip_building"] = _ms(started)
        span.set_attributes(
            {
                "debrief.candidate_clips": len(candidates),
                "debrief.clips": len(records),
                "debrief.clip_ids": [r["clip_id"] for r in records],
                "debrief.scores": [r["score"] for r in records],
                "debrief.total_seconds": total_seconds,
            }
        )

    debug["hits"] = len(hits)
    debug["talks"] = len(groups)
    timings["total"] = _ms(total_started)
    message = None
    if not records:
        message = "No clips fit this filter and budget."
    elif uncovered:
        message = f"No talk mentions {_names(uncovered)}; showing the rest of your stack."
    return {"total_seconds": total_seconds, "clips": records, "message": message, "debug": debug}


# --- Tools, talks, clip text -----------------------------------------------------------


def tool_counts(client: SearchClient) -> dict[str, int]:
    """Chunks per tool id across the whole `clips` index."""
    body = {"size": 0, "aggs": {"tools": {"terms": {"field": "tools", "size": 500}}}}
    response = client.search(CLIPS_INDEX, body)
    buckets = response.get("aggregations", {}).get("tools", {}).get("buckets", [])
    return {b["key"]: int(b["doc_count"]) for b in buckets}


def list_tools(client: SearchClient, min_chunks: int = MIN_TOOL_CHUNKS) -> list[dict[str, Any]]:
    """`[{id, name, chunk_count}]` for tools with at least `min_chunks` chunks, most first."""
    rows = [
        {"id": tool_id, "name": tool_name(tool_id), "chunk_count": n}
        for tool_id, n in tool_counts(client).items()
        if n >= min_chunks
    ]
    return sorted(rows, key=lambda r: (-r["chunk_count"], r["id"]))


def list_talks(client: SearchClient) -> list[dict[str, Any]]:
    """`[{talk_id, title, speakers}]` from the `talks` index, by title."""
    body = {"size": 1000, "_source": ["talk_id", "title", "speakers"], "query": {"match_all": {}}}
    response = client.search(TALKS_INDEX, body)
    talks = [
        {
            "talk_id": h["_source"]["talk_id"],
            "title": h["_source"].get("title") or "",
            "speakers": list(h["_source"].get("speakers") or []),
        }
        for h in response.get("hits", {}).get("hits", [])
    ]
    return sorted(talks, key=lambda t: (t["title"].lower(), t["talk_id"]))


def clip_text(client: SearchClient, clip_or_chunk_id: str) -> dict[str, Any] | None:
    """The clip's chunks loaded from the index by id, with its full cleaned text.

    Raises ValueError for a malformed id; returns None when no chunk is found.
    """
    chunk_ids = clipmod.parse_clip_id(clip_or_chunk_id)
    sources = client.mget(CLIPS_INDEX, chunk_ids)
    if not sources:
        return None
    chunks = sorted((Hit.from_source(s) for s in sources), key=lambda h: h.start_sec)
    if len({c.talk_id for c in chunks}) != 1:
        raise ValueError("chunks of a clip must come from one talk")
    clip = clipmod.Clip(tuple(chunks))
    start, end = clip.bounds
    return {
        "clip_id": clip.clip_id,
        "talk_id": clip.first.talk_id,
        "title": clip.first.title,
        "speakers": list(clip.first.speakers),
        "start_sec": start,
        "end_sec": end,
        "watch_url": clipmod.watch_url(clip.first.youtube_id, start),
        "text": clip.text,
    }
