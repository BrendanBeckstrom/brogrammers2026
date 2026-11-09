"""Hybrid search over the `clips` index and the playlist built from it (task 6).

One RRF request (keyword + semantic) ranks the chunks; the two halves also run on their
own as small `_id`-only queries, in parallel, so every clip says which half found it.
A fifth request, also in parallel, finds the stack's significant terms: a `significant_text`
aggregation over a sample of the chunks that match the tools ("what this conference said
about your stack"). Elastic sits behind `SearchClient`, so unit tests run against a fake.

Spans carry ids, counts, scores and timings only, never transcript text or the pain
sentence.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from opentelemetry import trace

from api import clips as clipmod
from api import rerank
from api.clips import Hit
from api.llm import LLMClient, LLMError
from ingest.tag import Tool, load_tools

CLIPS_INDEX = "clips"
TALKS_INDEX = "talks"
WINDOW = 100  # hits per half and per RRF request (rank_window_size)
MIN_TOOL_CHUNKS = 5  # /api/tools lists only tools with at least this many chunks
REQUEST_TIMEOUT_SEC = 10.0
# Rerank inside Elastic: Jina reorders the top RRF hits. 50 hits take about 1 s on the
# serverless project (100 take about 2.8 s); past the timeout the plain RRF order wins.
ELASTIC_RERANK_ID = ".jina-reranker-v3"
ELASTIC_RERANK_WINDOW = 50
ELASTIC_RERANK_TIMEOUT_SEC = 3.0
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
# Significant terms: JLH over a sample of the best keyword-half chunks, the whole index as
# background. The playlist waits for them only until SIGNIFICANT_TIMEOUT_SEC after the
# searches start, so they never hold up the answer.
SIGNIFICANT_TERMS = 8
SIGNIFICANT_CANDIDATES = 40  # asked of Elastic before the Python filter below
SIGNIFICANT_SHARD_SIZE = 200
SIGNIFICANT_MIN_DOCS = 3
SIGNIFICANT_TIMEOUT_SEC = 1.5
# Auto-caption filler and talk-speak that a sample of chunks can make look significant.
FILLER_WORDS = frozenset(
    """um uh umm uhh hmm ah oh like gonna wanna gotta yeah yep yes okay ok right so
    just really actually basically literally kind sort know mean think thing things stuff
    lot lots pretty going want get got let see look said say says well also much many
    had has have was were did does done been being able need make made way time times
    people folks guys everybody everyone something anything everything one two three
    first next last today slide slides talk question questions thank thanks awesome cool
    great good nice sure maybe probably different example data information use uses used
    using per create change""".split()
)
_TERM = re.compile(r"^[a-z][a-z0-9]{2,}$")
_WORD = re.compile(r"[^\W_]+")

Filter = Literal["all", "attended", "missed"]

tracer = trace.get_tracer("debrief")
log = logging.getLogger("debrief.search")
_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="search")


class SearchBackendError(RuntimeError):
    """Elastic failed or is not configured. The message never holds secrets.

    `unreachable` is True when Elastic could not be reached at all (connection
    refused, DNS, timeout): the demo-cache path treats that as "offline".
    """

    def __init__(self, message: str, *, unreachable: bool = False) -> None:
        super().__init__(message)
        self.unreachable = unreachable


def _is_unreachable(error: BaseException) -> bool:
    """Connection and timeout failures mean Elastic is unreachable, not that it errored."""
    from elastic_transport import ConnectionError as TransportConnectionError
    from elastic_transport import ConnectionTimeout

    return isinstance(error, (TransportConnectionError, ConnectionTimeout))


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
            raise SearchBackendError(
                f"Elastic search failed ({type(e).__name__})", unreachable=_is_unreachable(e)
            ) from e

    def mget(self, index: str, ids: Sequence[str]) -> list[dict[str, Any]]:
        try:
            docs = self._es.mget(index=index, ids=list(ids), source_includes=SOURCE_FIELDS)
        except Exception as e:
            raise SearchBackendError(
                f"Elastic mget failed ({type(e).__name__})", unreachable=_is_unreachable(e)
            ) from e
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


def reranker_body(body: dict[str, Any], inference_text: str) -> dict[str, Any]:
    """`body` with its retriever wrapped in Elastic's `text_similarity_reranker` (Jina)."""
    return {
        **body,
        "size": ELASTIC_RERANK_WINDOW,
        "retriever": {
            "text_similarity_reranker": {
                "retriever": body["retriever"],
                "field": "text",
                "inference_id": ELASTIC_RERANK_ID,
                "inference_text": inference_text,
                "rank_window_size": ELASTIC_RERANK_WINDOW,
            }
        },
    }


def merge_reranked(
    reranked: Mapping[str, Any], rrf_hits: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The reranked hits, then the RRF hits past the rerank window in their RRF order."""
    head = list(reranked.get("hits", {}).get("hits", []))
    seen = {h["_id"] for h in head}
    tail = [h for h in rrf_hits if h["_id"] not in seen]
    # Keep scores monotonic: tail hits rank below every reranked hit.
    floor = min((h.get("_score") or 0.0 for h in head), default=0.0)
    return head + [{**h, "_score": floor - 1.0 - i / max(1, len(tail))} for i, h in enumerate(tail)]


# --- Significant terms ----------------------------------------------------------------


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w}


@lru_cache(maxsize=1)
def tool_words() -> frozenset[str]:
    """Every word of every tool's name, id, aliases and caption spellings in tools.yaml."""
    words: set[str] = set()
    for tool in tool_catalog().values():
        for form in (tool.name, tool.id, *tool.terms):
            words |= _words(form)
    return frozenset(words)


_speakers: dict[str, frozenset[str]] = {}
_speakers_lock = threading.Lock()


def speaker_words(client: SearchClient) -> frozenset[str]:
    """Words of every speaker's name (the `talks` index), fetched once per process.

    Speakers say their names in every intro, so names look significant. On failure this
    returns an empty set and tries again next time.
    """
    with _speakers_lock:
        cached = _speakers.get("words")
    if cached is not None:
        return cached
    try:
        talks = list_talks(client)
    except Exception as e:  # SearchBackendError or a bad document: terms go on without it
        log.warning("speaker names unavailable: %s", type(e).__name__)
        return frozenset()
    words = frozenset(w for t in talks for s in t["speakers"] for w in _words(str(s)))
    with _speakers_lock:
        _speakers["words"] = words
    return words


def excluded_terms(client: SearchClient) -> list[str]:
    """What Elastic must never return as a significant term (sorted, for a stable body)."""
    return sorted(tool_words() | FILLER_WORDS | speaker_words(client))


def significant_terms_body(
    tool_ids: Sequence[str], terms: Sequence[str], exclude: Sequence[str]
) -> dict[str, Any]:
    """`significant_text` on `text` over a sample of the keyword-half chunks."""
    return {
        "size": 0,
        "track_total_hits": False,
        "query": keyword_query(tool_ids, terms),
        "aggs": {
            "sample": {
                "sampler": {"shard_size": SIGNIFICANT_SHARD_SIZE},
                "aggs": {
                    "keywords": {
                        "significant_text": {
                            "field": "text",
                            "size": SIGNIFICANT_CANDIDATES,
                            "min_doc_count": SIGNIFICANT_MIN_DOCS,
                            "filter_duplicate_text": True,
                            "jlh": {},
                            "exclude": list(exclude),
                        }
                    }
                },
            }
        },
    }


def parse_significant_terms(
    response: Mapping[str, Any], exclude: Sequence[str] | frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """At most SIGNIFICANT_TERMS `{term, score, doc_count}`, best first.

    Drops excluded words, terms under 3 characters or starting with a digit, and a plural
    whose singular already made the list (or the other way round).
    """
    excluded = set(exclude)
    sample = (response.get("aggregations") or {}).get("sample") or {}
    buckets = (sample.get("keywords") or {}).get("buckets") or []
    out: list[dict[str, Any]] = []
    stems: set[str] = set()
    for bucket in sorted(buckets, key=lambda b: -float(b.get("score") or 0.0)):
        term = str(bucket.get("key") or "").lower()
        if not _TERM.match(term) or term in excluded:
            continue
        stem = term[:-1] if term.endswith("s") and len(term) > 4 else term
        if stem in stems:
            continue
        stems.add(stem)
        out.append(
            {
                "term": term,
                "score": round(float(bucket.get("score") or 0.0), 3),
                "doc_count": int(bucket.get("doc_count") or 0),
            }
        )
        if len(out) == SIGNIFICANT_TERMS:
            break
    return out


def significant_terms(
    client: SearchClient, tool_ids: Sequence[str], terms: Sequence[str]
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    """(terms, milliseconds, the request body). Raises SearchBackendError."""
    with tracer.start_as_current_span("search.significant_terms") as span:
        started = time.perf_counter()
        exclude = excluded_terms(client)
        body = significant_terms_body(tool_ids, terms, exclude)
        response = client.search(CLIPS_INDEX, body)
        found = parse_significant_terms(response, exclude)
        elapsed = _ms(started)
        sample = (response.get("aggregations") or {}).get("sample") or {}
        span.set_attributes(
            {
                "debrief.tools": list(tool_ids),
                "debrief.sample_docs": int(sample.get("doc_count") or 0),
                "debrief.candidates": len((sample.get("keywords") or {}).get("buckets") or []),
                "debrief.excluded": len(exclude),
                "debrief.terms": len(found),
                "debrief.elastic_took_ms": int(response.get("took", 0)),
                "debrief.duration_ms": elapsed,
            }
        )
        return found, elapsed, body


def _collect_terms(
    future: Future[Any], search_started: float, timings: dict[str, float], debug: dict[str, Any]
) -> list[dict[str, Any]]:
    """The significant terms if they are ready by the deadline, else [] (never raises)."""
    wait = max(0.0, SIGNIFICANT_TIMEOUT_SEC - (time.perf_counter() - search_started))
    try:
        found, timings["significant_terms"], debug["terms_query"] = future.result(timeout=wait)
    except FutureTimeout:
        debug["terms_status"] = "timeout"
        log.warning("significant terms missed the deadline")
        return []
    except Exception as e:  # SearchBackendError or a malformed answer: no terms, same playlist
        debug["terms_status"] = "error"
        log.warning("significant terms failed: %s", type(e).__name__)
        return []
    debug["terms_status"] = "ok"
    return found


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


def submit(fn: Callable[[], Any]) -> Future[Any]:
    """Run `fn` on the pool inside a copy of the current context, so spans nest."""
    ctx = contextvars.copy_context()
    return _pool.submit(ctx.run, fn)


_submit = submit


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
    return {"total_seconds": 0, "clips": [], "terms": [], "message": message, "debug": debug}


def _names(tool_ids: Sequence[str]) -> str:
    names = [tool_name(t) for t in tool_ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def build_playlist(
    client: SearchClient, req: PlaylistRequest, llm: LLMClient | None = None
) -> dict[str, Any]:
    """Search, group by talk, build clips and fill the budget.

    With `req.rerank`, Elastic's Jina reranker reorders the hybrid hits (falling back to
    the plain RRF order on error or after ELASTIC_RERANK_TIMEOUT_SEC), then `llm` scores
    the top clips in parallel batches and writes their why lines (a failed batch keeps the
    template lines for its clips; if every batch fails, Elastic's order and the template
    lines). Returns the playlist response without
    `trace_id` (the route adds it).
    """
    total_started = time.perf_counter()
    tool_ids = list(dict.fromkeys(t.strip() for t in req.tools if t.strip()))
    timings: dict[str, float] = {}
    rerank_status = {"elastic": "off", "model": "off"}
    debug: dict[str, Any] = {
        "query": None,
        "timings_ms": timings,
        "hits": 0,
        "talks": 0,
        "rerank": rerank_status,
        "terms_query": None,
        "terms_status": "off",
        "uncovered_tools": [],
    }
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
    playlist_span = trace.get_current_span()

    search_started = time.perf_counter()
    reranked_f: Future[Any] | None = None
    if req.rerank:
        inference_text = ", ".join(tool_name(t) for t in tool_ids) + (f". {pain}" if pain else "")
        reranked_body = reranker_body(body, inference_text)
        reranked_f = _submit(
            lambda: _timed_search(client, "search.elastic_rerank", reranked_body, attrs)
        )
    hybrid_f = _submit(lambda: _timed_search(client, "search.hybrid", body, attrs))
    terms_f = _submit(lambda: significant_terms(client, tool_ids, terms))
    keyword_f = _submit(
        lambda: _timed_search(client, "search.keyword", ids_body(keyword, filter_, tool_ids), attrs)
    )
    semantic_f = _submit(
        lambda: _timed_search(client, "search.semantic", ids_body(semantic, filter_), attrs)
    )
    hybrid, timings["hybrid_search"] = hybrid_f.result()
    keyword_resp, timings["keyword_search"] = keyword_f.result()
    semantic_resp, timings["semantic_search"] = semantic_f.result()
    raw_hits: list[dict[str, Any]] = list(hybrid.get("hits", {}).get("hits", []))
    if reranked_f is not None:
        wait = max(0.0, ELASTIC_RERANK_TIMEOUT_SEC - (time.perf_counter() - search_started))
        try:
            reranked, timings["elastic_rerank"] = reranked_f.result(timeout=wait)
        except FutureTimeout:
            rerank_status["elastic"] = "fallback: timeout"
        except Exception:  # SearchBackendError, or anything else: keep the RRF order
            rerank_status["elastic"] = "fallback: error"
        else:
            raw_hits = merge_reranked(reranked, raw_hits)
            rerank_status["elastic"] = "ok"
            debug["query"] = reranked_body
        if rerank_status["elastic"] != "ok":
            log.warning("elastic rerank fell back: %s", rerank_status["elastic"])
        playlist_span.set_attribute("debrief.elastic_rerank", rerank_status["elastic"])
    timings["search_wall"] = _ms(search_started)

    covered = _tool_hits(keyword_resp)
    uncovered = [t for t in tool_ids if not covered.get(t)]
    debug["uncovered_tools"] = uncovered
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
        for raw in raw_hits:
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

    catalog = tool_catalog()
    names = {t: tool_name(t) for t in tool_ids}
    tool_term_map = {t: catalog[t].terms for t in tool_ids if t in catalog}

    def record(clip: clipmod.Clip) -> dict[str, Any]:
        return clipmod.clip_record(clip, tool_ids, names, tool_term_map, pain or None)

    with tracer.start_as_current_span("search.clips") as span:
        started = time.perf_counter()
        candidates = clipmod.build_clips(groups)
        timings["clip_building"] = _ms(started)
        span.set_attribute("debrief.candidate_clips", len(candidates))

    budget_sec = req.budget_minutes * 60
    chosen: list[clipmod.Clip] | None = None
    judged: dict[str, rerank.Judgement] = {}
    if req.rerank and llm is not None and candidates:
        with tracer.start_as_current_span("rerank.model") as span:
            started = time.perf_counter()
            top = candidates[: rerank.TOP_N]
            span.set_attribute("debrief.clips_sent", len(top))
            try:
                verdict = rerank.judge(
                    llm, [record(c) for c in top], top, tool_ids, names, tool_term_map, pain
                )
                chosen = rerank.choose(candidates, verdict.judged, budget_sec, tool_ids)
            except (LLMError, rerank.RerankError) as e:
                rerank_status["model"] = f"fallback: {type(e).__name__}"
                span.set_attributes({"debrief.fallback": True, "error.type": type(e).__name__})
                log.warning("model rerank fell back: %s", type(e).__name__)
            else:
                judged = verdict.judged
                failed = verdict.failed
                if failed:
                    outcomes = ", ".join(sorted({b.outcome for b in failed}))
                    rerank_status["model"] = (
                        f"partial: {len(failed)}/{len(verdict.batches)} batches fell back "
                        f"({outcomes})"
                    )
                else:
                    rerank_status["model"] = "ok"
                for batch in verdict.batches:
                    timings[f"rerank_batch_{batch.index}"] = batch.duration_ms
                below = sum(1 for j in judged.values() if j.score < rerank.SCORE_THRESHOLD)
                span.set_attributes(
                    {f"debrief.rerank.{k}": v for k, v in verdict.stats.items()}
                    | {
                        "debrief.fallback": False,
                        "debrief.rerank.below_threshold": below,
                        "debrief.rerank.batch_outcomes": [b.outcome for b in verdict.batches],
                    }
                )
            timings["rerank"] = _ms(started)
        playlist_span.set_attribute("debrief.model_rerank", rerank_status["model"])

    with tracer.start_as_current_span("search.budget") as span:
        started = time.perf_counter()
        if chosen is None:
            chosen = clipmod.fill_budget(candidates, budget_sec, tool_ids)
        records = [record(c) for c in chosen]
        if judged:
            for r in records:
                judgement = judged.get(r["clip_id"])
                r["score"] = round(judgement.score, 3) if judgement else 0.0
                if judgement and judgement.why:
                    r["why"] = judgement.why
        total_seconds = sum(r["end_sec"] - r["start_sec"] for r in records)
        timings["budget"] = _ms(started)
        span.set_attributes(
            {
                "debrief.clips": len(records),
                "debrief.clip_ids": [r["clip_id"] for r in records],
                "debrief.scores": [r["score"] for r in records],
                "debrief.total_seconds": total_seconds,
            }
        )

    found_terms = _collect_terms(terms_f, search_started, timings, debug)
    debug["hits"] = len(hits)
    debug["talks"] = len(groups)
    timings["total"] = _ms(total_started)
    message = None
    if not records:
        message = "No clips fit this filter and budget."
    elif uncovered:
        message = f"No talk mentions {_names(uncovered)}; showing the rest of your stack."
    return {
        "total_seconds": total_seconds,
        "clips": records,
        "terms": found_terms,
        "message": message,
        "debug": debug,
    }


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


def _clip_from_sources(sources: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One clip (full cleaned text) from its chunks' `_source`s. Raises ValueError."""
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


def clip_text(client: SearchClient, clip_or_chunk_id: str) -> dict[str, Any] | None:
    """The clip's chunks loaded from the index by id, with its full cleaned text.

    Raises ValueError for a malformed id; returns None when no chunk is found.
    """
    chunk_ids = clipmod.parse_clip_id(clip_or_chunk_id)
    sources = client.mget(CLIPS_INDEX, chunk_ids)
    if not sources:
        return None
    return _clip_from_sources(sources)


def resolve_clips(
    client: SearchClient, clip_ids: Sequence[str]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Load several clips from the index by id in one `mget` (the memo's only source).

    Returns (clips by the requested id, ids that are malformed or not fully in the index).
    """
    wanted: dict[str, list[str]] = {}
    unknown: list[str] = []
    for clip_id in dict.fromkeys(clip_ids):
        try:
            wanted[clip_id] = clipmod.parse_clip_id(clip_id)
        except ValueError:
            unknown.append(clip_id)
    all_chunks = list(dict.fromkeys(c for chunks in wanted.values() for c in chunks))
    by_id = (
        {str(s["chunk_id"]): s for s in client.mget(CLIPS_INDEX, all_chunks)} if all_chunks else {}
    )
    found: dict[str, dict[str, Any]] = {}
    for clip_id, chunk_ids in wanted.items():
        if not all(c in by_id for c in chunk_ids):
            unknown.append(clip_id)
            continue
        clip = _clip_from_sources([by_id[c] for c in chunk_ids])
        if clip["clip_id"] != clip_id:  # not the canonical form the playlist hands out
            unknown.append(clip_id)
            continue
        found[clip_id] = clip
    return found, unknown
