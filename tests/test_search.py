"""Task 6: search, clip building and the playlist API, all against a fake Elastic."""

import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api import clips as clipmod
from api import routes_search, search
from api.clips import Hit
from api.main import app
from tests import golden

# --- Synthetic corpus ----------------------------------------------------------------
# Ten talks of fourteen 75-second chunks every 60 seconds (15 seconds of overlap). The
# text is made up; tools sit in fixed chunks so each golden stack covers several talks.

NAMES = {
    "argo-cd": "Argo CD",
    "prometheus": "Prometheus",
    "opentelemetry": "OpenTelemetry",
    "istio": "Istio",
    "envoy": "Envoy",
}
PLACEMENT: dict[str, tuple[range, tuple[int, ...]]] = {
    "argo-cd": (range(0, 7), (1, 2, 7)),
    "prometheus": (range(0, 10), (4, 11)),
    "opentelemetry": (range(3, 10), (9, 10)),
    "istio": (range(3, 10), (12,)),
    "envoy": (range(3, 10), (5, 12)),
}
TALK_IDS = [f"talk{chr(ord('a') + k)}0000" for k in range(10)]


def make_chunk(k: int, i: int) -> dict[str, Any]:
    talk_id = TALK_IDS[k]
    tools = sorted(t for t, (talks, chunks) in PLACEMENT.items() if k in talks and i in chunks)
    sentences = [f"Part {i} of talk {k} covers platform work.", f"We measured step {i} closely."]
    sentences += [f"In part {i} we run {NAMES[t]} in production." for t in tools]
    sentences.append(f"That closes part {i}.")
    start = 60 * i
    return {
        "chunk_id": f"{talk_id}-{start}",
        "talk_id": talk_id,
        "youtube_id": talk_id,
        "title": f"Talk {k}",
        "speakers": [f"Speaker {k}"],
        "track": "Track",
        "start_sec": start,
        "end_sec": start + 75,
        "text": " ".join(sentences),
        "tools": tools,
        "talk_label": None,
    }


CHUNKS = [make_chunk(k, i) for k in range(10) for i in range(14)]
TALKS = [
    {"talk_id": t, "youtube_id": t, "title": f"Talk {k}", "speakers": [f"Speaker {k}"]}
    for k, t in enumerate(TALK_IDS)
]


class FakeSearchClient:
    """Interprets the handful of request shapes api/search.py sends."""

    def __init__(self, chunks: list[dict[str, Any]], talks: list[dict[str, Any]]) -> None:
        self.chunks = {c["chunk_id"]: c for c in chunks}
        self.talks = talks
        self.bodies: list[tuple[str, dict[str, Any]]] = []

    def mget(self, index: str, ids: list[str]) -> list[dict[str, Any]]:
        assert index == "clips"
        return [self.chunks[i] for i in ids if i in self.chunks]

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append((index, body))
        if index == "talks":
            return {"hits": {"hits": [{"_id": t["talk_id"], "_source": t} for t in self.talks]}}
        retriever = body.get("retriever")
        if retriever is None:  # chunks-per-tool aggregation
            counts = Counter(t for c in self.chunks.values() for t in c["tools"])
            buckets = [{"key": k, "doc_count": n} for k, n in counts.most_common()]
            return {"hits": {"hits": []}, "aggregations": {"tools": {"buckets": buckets}}}
        if "rrf" in retriever:
            return self._rrf(body, retriever["rrf"])
        if "text_similarity_reranker" in retriever:
            return self._reranked(body, retriever["text_similarity_reranker"])
        ids = self._standard(retriever["standard"])[: body["size"]]
        response: dict[str, Any] = {"took": 1, "hits": {"hits": [{"_id": i} for i in ids]}}
        if "aggs" in body:
            include = set(body["aggs"]["tools"]["aggs"]["per_tool"]["terms"]["include"])
            counts = Counter(t for i in ids for t in self.chunks[i]["tools"] if t in include)
            buckets = [{"key": k, "doc_count": n} for k, n in counts.items()]
            response["aggregations"] = {"tools": {"per_tool": {"buckets": buckets}}}
        return response

    def _rrf(self, body: dict[str, Any], rrf: dict[str, Any]) -> dict[str, Any]:
        scores: dict[str, float] = defaultdict(float)
        for sub in rrf["retrievers"]:
            for rank, chunk_id in enumerate(
                self._standard(sub["standard"])[: rrf["rank_window_size"]], 1
            ):
                scores[chunk_id] += 1 / (60 + rank)
        order = sorted(scores, key=lambda c: (-scores[c], c))[: body["size"]]
        phrases = self._phrases(
            body.get("highlight", {}).get("fields", {}).get("text", {}).get("highlight_query", {})
        )
        hits = []
        for chunk_id in order:
            source = self.chunks[chunk_id]
            hit: dict[str, Any] = {"_id": chunk_id, "_score": scores[chunk_id], "_source": source}
            for sentence in clipmod.split_sentences(source["text"]):
                if any(p in sentence.lower() for p in phrases):
                    hit["highlight"] = {"text": [f"<em>{sentence}</em>"]}
                    break
            hits.append(hit)
        return {"took": 2, "hits": {"hits": hits}}

    def _reranked(self, body: dict[str, Any], reranker: dict[str, Any]) -> dict[str, Any]:
        """Like Elastic's text_similarity_reranker: reorder the inner top N by word overlap."""
        assert reranker["field"] == "text" and reranker["inference_id"].startswith(".jina")
        window = reranker["rank_window_size"]
        inner = self._rrf({**body, "size": window}, reranker["retriever"]["rrf"])
        words = set(re.findall(r"\w{4,}", reranker["inference_text"].lower()))
        hits = inner["hits"]["hits"][:window]
        for rank, hit in enumerate(hits):
            overlap = len(words & set(re.findall(r"\w{4,}", hit["_source"]["text"].lower())))
            hit["_score"] = overlap - rank / 1000
        hits.sort(key=lambda h: -h["_score"])
        return {"took": 3, "hits": {"hits": hits[: body["size"]]}}

    @staticmethod
    def _phrases(query: dict[str, Any]) -> list[str]:
        return [
            s["match_phrase"]["text"]
            for s in query.get("bool", {}).get("should", [])
            if "match_phrase" in s
        ]

    def _standard(self, standard: dict[str, Any]) -> list[str]:
        docs = [c for c in self.chunks.values() if self._passes(c, standard.get("filter"))]
        query = standard["query"]
        if "semantic" in query:  # like kNN: every doc, nearest first
            words = set(re.findall(r"\w{4,}", query["semantic"]["query"].lower()))
            overlap = {
                c["chunk_id"]: len(words & set(re.findall(r"\w{4,}", c["text"].lower())))
                for c in docs
            }
            return sorted(overlap, key=lambda i: (-overlap[i], i))
        tools: set[str] = set()
        for clause in query["bool"]["should"]:
            if "terms" in clause:
                tools |= set(clause["terms"]["tools"])
        phrases = self._phrases(query)
        scored = {
            c["chunk_id"]: 2 * len(tools & set(c["tools"]))
            + sum(p in c["text"].lower() for p in phrases)
            for c in docs
        }
        return sorted((i for i, s in scored.items() if s > 0), key=lambda i: (-scored[i], i))

    @staticmethod
    def _passes(chunk: dict[str, Any], filter_: dict[str, Any] | None) -> bool:
        if filter_ is None:
            return True
        if "terms" in filter_:
            return chunk["talk_id"] in filter_["terms"]["talk_id"]
        excluded = filter_["bool"]["must_not"][0]["terms"]["talk_id"]
        return chunk["talk_id"] not in excluded


@pytest.fixture
def fake() -> FakeSearchClient:
    return FakeSearchClient(CHUNKS, TALKS)


@pytest.fixture
def api(fake: FakeSearchClient) -> Iterator[TestClient]:
    app.dependency_overrides[routes_search.search_client] = lambda: fake
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("debrief")
    monkeypatch.setattr(search, "tracer", tracer)
    monkeypatch.setattr(routes_search, "tracer", tracer)
    return exporter


def hit(chunk_id: str, start: int, end: int, score: float = 1.0, **kw: Any) -> Hit:
    talk_id = chunk_id.rsplit("-", 1)[0]
    source = {"chunk_id": chunk_id, "talk_id": talk_id, "start_sec": start, "end_sec": end}
    source.update(kw)
    return Hit.from_source(source, score=score)


# --- Clip building -------------------------------------------------------------------


def test_watch_url_matches_start() -> None:
    assert clipmod.watch_url("abc123", 870) == "https://www.youtube.com/watch?v=abc123&t=870s"


def test_overlapping_and_near_chunks_merge_with_two_second_preroll() -> None:
    clips = clipmod.merge_talk(
        [hit("t-100", 100, 175), hit("t-160", 160, 235), hit("t-250", 250, 325)]
    )
    assert len(clips) == 1  # overlap, then a 15-second gap
    assert clips[0].bounds == (98, 325)
    assert clips[0].clip_id == "t-100~160~250"


def test_far_chunks_stay_separate_and_start_at_zero_floor() -> None:
    clips = clipmod.merge_talk([hit("t-1", 1, 76), hit("t-200", 200, 275)])
    assert [c.bounds for c in clips] == [(0, 76), (198, 275)]


def test_clip_length_is_capped_and_floored() -> None:
    long = clipmod.Clip((hit("t-0", 10, 400),))
    short = clipmod.Clip((hit("t-0", 10, 30),))
    assert long.bounds == (8, 248)
    assert short.bounds == (8, 68)


def test_late_chunk_starts_a_new_clip_instead_of_being_cut() -> None:
    hits = [hit("t-100", 100, 199), hit("t-184", 184, 283), hit("t-288", 288, 380)]
    clips = clipmod.merge_talk(hits)
    assert [c.clip_id for c in clips] == ["t-100~184", "t-288"]
    assert all(60 <= c.length <= 240 for c in clips)


def test_chunk_extending_past_cap_starts_a_new_clip() -> None:
    clips = clipmod.merge_talk([hit("t-100", 100, 250), hit("t-260", 260, 340)])
    assert [c.clip_id for c in clips] == ["t-100", "t-260"]
    assert clips[1].bounds == (258, 340)


def test_group_by_talk_keeps_three_best_per_talk() -> None:
    hits = [hit(f"a-{i}", i * 100, i * 100 + 75, score=10 - i) for i in range(5)]
    hits.insert(2, hit("b-0", 0, 75, score=7.5))
    groups = clipmod.group_by_talk(hits)
    assert [len(g) for g in groups] == [3, 1]
    assert [h.chunk_id for h in groups[0]] == ["a-0", "a-1", "a-2"]


def test_merged_text_drops_overlap_repeats() -> None:
    a = hit("t-0", 0, 75, text="One. Two. Three.")
    b = hit("t-60", 60, 135, text="Three. Four.")
    assert clipmod.merged_text([b, a]) == "One. Two. Three. Four."


def test_quote_starts_at_highlight_and_stays_short() -> None:
    text = "Intro words here. We tuned Argo CD sync waves. It cut sync time a lot. Then more. End."
    quote = clipmod.choose_quote(text, "We tuned <em>Argo</em> <em>CD</em> sync waves.")
    assert quote == "We tuned Argo CD sync waves. It cut sync time a lot. Then more."
    long = " ".join(
        f"Sentence number {i} is here and it is fairly long to read." for i in range(20)
    )
    quote = clipmod.choose_quote(long, None)
    assert len(quote) <= 300 and quote.startswith("Sentence number 0")
    assert 2 <= quote.count(".") <= 3


def test_quote_truncates_one_huge_sentence() -> None:
    quote = clipmod.choose_quote("word " * 200, None)
    assert len(quote) <= 300 and quote.endswith("…")


def test_clip_id_round_trip_and_rejects_junk() -> None:
    assert clipmod.parse_clip_id("abc_D-12-872~932~990") == [
        "abc_D-12-872",
        "abc_D-12-932",
        "abc_D-12-990",
    ]
    assert clipmod.parse_clip_id("abc-872") == ["abc-872"]
    for bad in ["", "abc", "abc-1~x", "abc-1~2~3~4", "../etc-1"]:
        with pytest.raises(ValueError):
            clipmod.parse_clip_id(bad)


def test_template_why_names_the_tool_and_counts() -> None:
    why = clipmod.template_why(
        "We use ArgoCD. Argo CD is great. Argo-CD again.",
        ["argo-cd", "prometheus"],
        {"argo-cd": "Argo CD", "prometheus": "Prometheus"},
        {"argo-cd": ("argo cd", "argocd")},
    )
    assert why == "Mentions Argo CD 3 times in this clip (also Prometheus)"


def test_fill_budget_skips_overflow_and_caps_unmatched() -> None:
    def clip(name: str, length: int, score: float, tools: tuple[str, ...]) -> clipmod.Clip:
        return clipmod.Clip((hit(f"{name}-10", 10, 8 + length, score=score, tools=list(tools)),))

    unmatched = [clip("u1", 100, 20, ()), clip("u2", 100, 19, ())]
    matched = [clip(f"m{i}", 100, 10 - i, ("x",)) for i in range(4)]
    chosen = clipmod.fill_budget(matched + unmatched, budget_sec=1000, requested_tools=["x"])
    # Matched clips first; u2 would make 2 of 6 unmatched (over 1 in 5). Score order out.
    assert [c.first.talk_id for c in chosen] == ["u1", "m0", "m1", "m2", "m3"]

    matched.insert(1, clip("big", 240, 9.5, ("x",)))
    chosen = clipmod.fill_budget(matched + unmatched, budget_sec=600, requested_tools=["x"])
    assert [c.first.talk_id for c in chosen] == ["m0", "big", "m1", "m2"]  # m3 would overflow
    assert sum(c.length for c in chosen) <= 600


# --- Query bodies --------------------------------------------------------------------


def test_rrf_body_follows_the_brief() -> None:
    terms = search.tool_terms(["argo-cd", "prometheus"])
    assert "argo cd" in terms and "argocd" in terms and "prometheus" in terms
    body = search.rrf_body(
        search.keyword_query(["argo-cd", "prometheus"], terms),
        search.semantic_query("our Argo CD syncs are slow"),
        None,
        terms,
    )
    rrf = body["retriever"]["rrf"]
    assert body["size"] == 100 and rrf["rank_window_size"] == 100
    keyword, semantic = (r["standard"]["query"] for r in rrf["retrievers"])
    assert keyword["bool"]["should"][0] == {
        "terms": {"tools": ["argo-cd", "prometheus"], "boost": 2}
    }
    assert {"match_phrase": {"text": "argo cd"}} in keyword["bool"]["should"]
    assert semantic == {
        "semantic": {"field": "text_semantic", "query": "our Argo CD syncs are slow"}
    }


def test_semantic_half_uses_tool_names_without_pain(
    api: TestClient, fake: FakeSearchClient
) -> None:
    api.post("/api/playlist", json={"tools": ["istio", "envoy"], "rerank": False})
    semantic = [
        b for _, b in fake.bodies if "semantic" in str(b) and "rrf" not in b.get("retriever", {})
    ]
    assert semantic[0]["retriever"]["standard"]["query"]["semantic"]["query"] == "Istio, Envoy"


# --- Routes --------------------------------------------------------------------------


def test_playlist_contract_and_debug(api: TestClient) -> None:
    body = {
        "tools": ["argo-cd", "prometheus"],
        "pain": "our Argo CD syncs are slow",
        "rerank": True,
    }
    response = api.post("/api/playlist", json=body)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"total_seconds", "clips", "trace_id", "message", "debug"}
    assert data["trace_id"] is None  # no tracer provider configured: no-op
    clip = data["clips"][0]
    assert set(clip) == {
        "clip_id", "talk_id", "title", "speakers", "start_sec", "end_sec", "watch_url",
        "quote", "why", "matched_tools", "matched_by", "talk_label", "score",
    }  # fmt: skip
    assert clip["matched_by"] in {"keyword", "semantic", "both"}
    assert clip["why"].startswith("Mentions ")
    reranker = data["debug"]["query"]["retriever"]["text_similarity_reranker"]
    assert reranker["inference_id"] == ".jina-reranker-v3" and reranker["field"] == "text"
    assert reranker["retriever"]["rrf"]["rank_window_size"] == 100
    assert data["debug"]["rerank"]["elastic"] == "ok"
    assert {"keyword_search", "semantic_search", "grouping", "clip_building", "total"} <= set(
        data["debug"]["timings_ms"]
    )
    scores = [c["score"] for c in data["clips"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize(
    "stack", [s for s in golden.load_stacks() if not s.get("skip")], ids=lambda s: s["name"]
)
def test_golden_structural_checks_on_fake(api: TestClient, stack: dict[str, Any]) -> None:
    if stack.get("attended_talk_ids"):  # the real talk id is not in the fake corpus
        stack = {**stack, "attended_talk_ids": [TALK_IDS[0]]}
    response = api.post("/api/playlist", json=golden.playlist_request(stack))
    assert response.status_code == 200
    failed = [c for c in golden.check_playlist(stack, response.json()) if not c.ok]
    assert not failed, failed


def test_golden_stacks_file_is_complete() -> None:
    stacks = {s["name"]: s for s in golden.load_stacks()}
    assert set(stacks) == {
        "gitops",
        "observability",
        "mesh",
        "from-file",
        "missed-only",
        "no-coverage",
    }
    assert "FILL_IN" not in str(stacks["missed-only"])
    assert stacks["from-file"]["skip"]


def test_no_coverage_returns_no_weak_matches(api: TestClient) -> None:
    data = api.post("/api/playlist", json={"tools": ["a-tool-with-no-chunks"]}).json()
    assert data["clips"] == [] and data["total_seconds"] == 0
    assert "No talk mentions" in data["message"]


def test_attended_filter_keeps_only_attended(api: TestClient) -> None:
    body = {"tools": ["prometheus"], "attended_talk_ids": TALK_IDS[:2], "filter": "attended"}
    data = api.post("/api/playlist", json=body).json()
    assert data["clips"] and {c["talk_id"] for c in data["clips"]} <= set(TALK_IDS[:2])


def test_attended_filter_without_talks_explains(api: TestClient) -> None:
    data = api.post("/api/playlist", json={"tools": ["prometheus"], "filter": "attended"}).json()
    assert data["clips"] == [] and data["message"]


def test_partly_covered_stack_says_which_tool_is_missing(api: TestClient) -> None:
    data = api.post("/api/playlist", json={"tools": ["istio", "a-tool-with-no-chunks"]}).json()
    assert data["clips"] and "a-tool-with-no-chunks" in data["message"]


def test_playlist_validates_input(api: TestClient) -> None:
    assert api.post("/api/playlist", json={"tools": ["x"], "filter": "nope"}).status_code == 422
    assert api.post("/api/playlist", json={"tools": ["x"], "budget_minutes": 0}).status_code == 422


def test_tools_lists_tools_with_five_chunks(api: TestClient) -> None:
    rows = api.get("/api/tools").json()
    assert rows[0] == {"id": "argo-cd", "name": "Argo CD", "chunk_count": 21}
    assert {r["id"] for r in rows} == set(PLACEMENT)
    assert all(r["chunk_count"] >= 5 for r in rows)


def test_tools_hides_tools_under_five_chunks(fake: FakeSearchClient) -> None:
    fake.chunks["talka0000-0"] = {**fake.chunks["talka0000-0"], "tools": ["helm"]}
    assert "helm" not in {r["id"] for r in search.list_tools(fake)}


def test_talks_lists_every_talk(api: TestClient) -> None:
    rows = api.get("/api/talks").json()
    assert len(rows) == 10
    assert rows[0] == {"talk_id": TALK_IDS[0], "title": "Talk 0", "speakers": ["Speaker 0"]}


def test_clip_route_returns_full_text(api: TestClient) -> None:
    data = api.get(f"/api/clips/{TALK_IDS[0]}-60~120").json()
    assert data["clip_id"] == f"{TALK_IDS[0]}-60~120"
    assert (data["start_sec"], data["end_sec"]) == (58, 195)
    assert data["watch_url"].endswith("&t=58s")
    assert "Part 1 of talk 0" in data["text"] and "Part 2 of talk 0" in data["text"]
    assert data["text"].count("That closes part 1.") == 1


def test_clip_route_accepts_chunk_id_and_rejects_unknown(api: TestClient) -> None:
    assert api.get(f"/api/clips/{TALK_IDS[1]}-0").json()["start_sec"] == 0
    assert api.get("/api/clips/nope-999").status_code == 404
    assert api.get("/api/clips/not an id").status_code == 404


def test_playlist_clip_ids_resolve_through_clip_route(api: TestClient) -> None:
    data = api.post("/api/playlist", json={"tools": ["istio", "envoy"]}).json()
    for clip in data["clips"][:3]:
        full = api.get(f"/api/clips/{clip['clip_id']}").json()
        assert (full["start_sec"], full["end_sec"]) == (clip["start_sec"], clip["end_sec"])


def test_missing_elastic_config_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELASTIC_URL", raising=False)
    monkeypatch.delenv("ELASTIC_API_KEY", raising=False)
    search.get_client.cache_clear()
    try:
        assert TestClient(app).get("/api/tools").status_code == 503
    finally:
        search.get_client.cache_clear()


def test_backend_error_is_502(
    api: TestClient, fake: FakeSearchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(index: str, body: dict[str, Any]) -> dict[str, Any]:
        raise search.SearchBackendError("Elastic search failed (ConnectionError)")

    monkeypatch.setattr(fake, "search", boom)
    assert api.post("/api/playlist", json={"tools": ["istio"]}).status_code == 502
    assert api.get("/api/talks").status_code == 502


# --- Spans ---------------------------------------------------------------------------


def test_spans_cover_each_step_and_carry_no_text(
    api: TestClient, spans: InMemorySpanExporter
) -> None:
    pain = "our Argo CD syncs are slow"
    data = api.post("/api/playlist", json={"tools": ["argo-cd"], "pain": pain}).json()
    finished = spans.get_finished_spans()
    names = {s.name for s in finished}
    assert {
        "api.playlist",
        "search.hybrid",
        "search.keyword",
        "search.semantic",
        "search.group",
        "search.clips",
    } <= names
    assert len({s.context.trace_id for s in finished}) == 1  # threads stay in one trace
    assert data["trace_id"] == format(finished[0].context.trace_id, "032x")
    text_bits = {pain, "production", "platform work"}
    for span in finished:
        for value in span.attributes.values():
            flat = " ".join(map(str, value)) if isinstance(value, tuple) else str(value)
            assert not any(bit in flat for bit in text_bits), (span.name, value)


def test_every_route_has_a_span(api: TestClient, spans: InMemorySpanExporter) -> None:
    api.get("/api/tools")
    api.get("/api/talks")
    api.get(f"/api/clips/{TALK_IDS[0]}-0")
    assert {"api.tools", "api.talks", "api.clip"} <= {s.name for s in spans.get_finished_spans()}
