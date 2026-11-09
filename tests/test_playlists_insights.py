"""Significant terms, saved playlists (shared across replicas), the query log and insights,
corrected transcripts.

All against a fake Elastic (tests/test_search.py's corpus) and `FakeStore`; no network.
"""

from __future__ import annotations

import copy
import json
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api import (
    corrections,
    demo_cache,
    insights,
    playlists,
    routes_playlists,
    routes_search,
    search,
    store,
)
from api.main import app
from api.search import SearchBackendError
from tests import golden
from tests.test_search import CHUNKS, TALK_IDS, TALKS, FakeSearchClient

PAIN = "our Argo CD syncs are slow"
GITOPS = {"tools": ["argo-cd", "prometheus"], "pain": PAIN, "rerank": False}


class FakeStore:
    """Debrief's own indexes in memory: put/get/count and `terms` aggregations."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict[str, Any]]] = {}
        self.ensured: list[str] = []
        self.fail = False

    def ensure_index(self, index: str) -> None:
        assert index in store.MAPPINGS
        self.ensured.append(index)

    def put(
        self, index: str, doc_id: str | None, doc: dict[str, Any], create: bool = False
    ) -> None:
        if self.fail:
            raise SearchBackendError("Elastic index failed (ConnectionError)", unreachable=True)
        self.ensure_index(index)
        mapping = store.MAPPINGS[index]
        if mapping.get("dynamic") == "strict":
            assert set(doc) <= set(mapping["properties"]), set(doc) - set(mapping["properties"])
        docs = self.docs.setdefault(index, {})
        doc_id = doc_id or f"auto-{len(docs)}"
        if create and doc_id in docs:
            raise store.DocumentExists(doc_id)
        docs[doc_id] = json.loads(json.dumps(doc))  # as Elastic would keep it: plain JSON

    def get(self, index: str, doc_id: str) -> dict[str, Any] | None:
        if self.fail:
            raise SearchBackendError("Elastic get failed (ConnectionError)", unreachable=True)
        doc = self.docs.get(index, {}).get(doc_id)
        return copy.deepcopy(doc) if doc is not None else None

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.fail:
            raise SearchBackendError("Elastic search failed (ConnectionError)", unreachable=True)
        if index not in self.docs:
            return {}
        docs = list(self.docs[index].values())
        if "query" in body:  # bool filter of term clauses, newest first (the answers lookup)
            for clause in body["query"]["bool"]["filter"]:
                ((field, value),) = clause["term"].items()
                docs = [d for d in docs if value in _values(d.get(field))]
            docs.sort(key=lambda d: str(d.get("ts")), reverse=True)
            hits = [{"_source": copy.deepcopy(d)} for d in docs[: body.get("size", 10)]]
            return {"hits": {"total": {"value": len(docs)}, "hits": hits}}
        aggs: dict[str, Any] = {}
        for name, agg in (body.get("aggs") or {}).items():
            terms = agg["terms"]
            counts = Counter(v for d in docs for v in d.get(terms["field"]) or [])
            aggs[name] = {
                "buckets": [
                    {"key": k, "doc_count": n}
                    for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
                ][: terms["size"]]
            }
        return {"hits": {"total": {"value": len(docs)}, "hits": []}, "aggregations": aggs}

    def count(self, index: str) -> int:
        if self.fail:
            raise SearchBackendError("Elastic count failed (ConnectionError)", unreachable=True)
        return len(self.docs.get(index, {}))


def _values(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else [value]


@pytest.fixture
def fake() -> FakeSearchClient:
    return FakeSearchClient(CHUNKS, TALKS)


@pytest.fixture
def saved() -> FakeStore:
    return FakeStore()


@pytest.fixture
def api(
    fake: FakeSearchClient, saved: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr(playlists, "SHARED_RETRY_DELAYS", (0.0,))
    playlists.recent.clear()
    app.dependency_overrides[routes_search.search_client] = lambda: fake
    app.dependency_overrides[routes_playlists.playlist_store] = lambda: saved
    app.dependency_overrides[routes_search.query_log_store] = lambda: saved
    yield TestClient(app)
    app.dependency_overrides.clear()
    playlists.recent.clear()


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("debrief")
    for module in (search, routes_search, routes_playlists, insights, playlists):
        monkeypatch.setattr(module, "tracer", tracer)
    return exporter


def flat_attributes(exporter: InMemorySpanExporter) -> str:
    return " ".join(
        " ".join(map(str, v)) if isinstance(v, tuple) else str(v)
        for s in exporter.get_finished_spans()
        for v in s.attributes.values()
    )


# --- Significant terms ------------------------------------------------------------------


def test_significant_terms_body_samples_and_excludes(fake: FakeSearchClient) -> None:
    exclude = search.excluded_terms(fake)
    body = search.significant_terms_body(["argo-cd"], search.tool_terms(["argo-cd"]), exclude)
    sampler = body["aggs"]["sample"]
    assert body["size"] == 0 and sampler["sampler"] == {"shard_size": 200}
    agg = sampler["aggs"]["keywords"]["significant_text"]
    assert agg["field"] == "text" and agg["filter_duplicate_text"] is True
    assert body["query"] == search.keyword_query(["argo-cd"], search.tool_terms(["argo-cd"]))
    words = set(agg["exclude"])
    # Tool names, ids, aliases and caption spellings; caption filler; speaker names.
    assert {"argo", "cd", "argocd", "prometheus", "psyllium", "hotel", "otel", "envoy"} <= words
    assert {"um", "uh", "like", "gonna", "yeah"} <= words
    assert "speaker" in words


def test_parse_significant_terms_filters_and_caps() -> None:
    keys = ["traffic", "x", "443", "argo", "routing", "metrics", "metric", "ab", "routes"]
    keys += [f"term{i}" for i in range(20)]
    buckets = [
        {"key": k, "doc_count": 10 - i % 10, "score": 5.0 - i * 0.1} for i, k in enumerate(keys)
    ]
    response = {"aggregations": {"sample": {"doc_count": 99, "keywords": {"buckets": buckets}}}}
    found = search.parse_significant_terms(response, {"argo"})
    assert [t["term"] for t in found] == [
        "traffic", "routing", "metrics", "routes", "term0", "term1", "term2", "term3",
    ]  # fmt: skip
    assert len(found) == search.SIGNIFICANT_TERMS
    assert found[0] == {"term": "traffic", "score": 5.0, "doc_count": 10}
    assert search.parse_significant_terms({}) == []


def test_playlist_carries_terms_and_timing(api: TestClient, spans: InMemorySpanExporter) -> None:
    data = api.post("/api/playlist", json=GITOPS).json()
    terms = data["terms"]
    assert 0 < len(terms) <= 8
    assert all(set(t) == {"term", "score", "doc_count"} for t in terms)
    assert "production" in {t["term"] for t in terms}  # only tool chunks say it
    assert not {"argo", "cd", "prometheus", "speaker"} & {t["term"] for t in terms}
    assert data["debug"]["terms_status"] == "ok"
    assert "significant_terms" in data["debug"]["timings_ms"]
    assert "significant_text" in json.dumps(data["debug"]["terms_query"])
    assert golden.terms_check(data).ok
    span = next(s for s in spans.get_finished_spans() if s.name == "search.significant_terms")
    assert span.attributes["debrief.terms"] == len(terms)
    assert span.attributes["debrief.sample_docs"] > 0
    for bit in (PAIN, "production", "platform work"):
        assert bit not in flat_attributes(spans)


def test_terms_failure_or_timeout_leaves_the_playlist_alone(
    api: TestClient, fake: FakeSearchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fake.search

    def broken(index: str, body: dict[str, Any]) -> dict[str, Any]:
        if "sample" in body.get("aggs", {}):
            raise SearchBackendError("Elastic search failed (BadRequestError)")
        return real(index, body)

    monkeypatch.setattr(fake, "search", broken)
    data = api.post("/api/playlist", json=GITOPS).json()
    assert data["clips"] and data["terms"] == []
    assert data["debug"]["terms_status"] == "error"

    def slow(index: str, body: dict[str, Any]) -> dict[str, Any]:
        if "sample" in body.get("aggs", {}):
            time.sleep(0.3)
        return real(index, body)

    monkeypatch.setattr(fake, "search", slow)
    monkeypatch.setattr(search, "SIGNIFICANT_TIMEOUT_SEC", 0.05)
    data = api.post("/api/playlist", json=GITOPS).json()
    assert data["clips"] and data["terms"] == []
    assert data["debug"]["terms_status"] == "timeout"


def test_empty_playlist_has_empty_terms(api: TestClient) -> None:
    data = api.post("/api/playlist", json={"tools": ["a-tool-with-no-chunks"]}).json()
    assert data["clips"] == [] and data["terms"] == []


def test_golden_terms_check() -> None:
    assert golden.terms_check({}).ok  # an old cached answer without terms
    assert golden.terms_check({"terms": []}).ok
    assert golden.terms_check({"terms": [{"term": "a", "score": 1.0, "doc_count": 2}]}).ok
    assert not golden.terms_check({"terms": None}).ok
    assert not golden.terms_check({"terms": [{"term": "a"}]}).ok


# --- Query log and insights -------------------------------------------------------------


def test_every_playlist_logs_one_doc_without_the_pain(api: TestClient, saved: FakeStore) -> None:
    api.post("/api/playlist", json=GITOPS)
    api.post("/api/playlist", json={"tools": ["istio", "terraform"], "rerank": False})
    api.post("/api/playlist", json={"tools": ["a-tool-with-no-chunks", "Not A Slug!"]})
    docs = list(saved.docs["queries"].values())
    assert len(docs) == 3
    first, partial, empty = docs
    assert set(first) == set(store.QUERIES_MAPPINGS["properties"])
    assert first["tools"] == ["argo-cd", "prometheus"] and first["has_pain"] is True
    assert first["filter"] == "all" and first["rerank"] is False
    assert first["clip_count"] > 0 and first["empty"] is False and first["duration_ms"] > 0
    assert first["uncovered_tools"] == []
    assert partial["uncovered_tools"] == ["terraform"] and not partial["empty"]
    assert empty["tools"] == ["a-tool-with-no-chunks"] and empty["empty"] is True
    assert empty["uncovered_tools"] == ["a-tool-with-no-chunks"] and empty["has_pain"] is False
    assert "slow" not in json.dumps(saved.docs)


def test_query_log_failure_never_breaks_the_playlist(api: TestClient, saved: FakeStore) -> None:
    saved.fail = True
    response = api.post("/api/playlist", json=GITOPS)
    assert response.status_code == 200 and response.json()["clips"]
    assert insights.log_query(None, {"tools": []}) is False


def test_query_log_is_off_without_elastic_config() -> None:
    store.get_store.cache_clear()
    try:
        assert routes_search.query_log_store() is None  # conftest cleared ELASTIC_*
    finally:
        store.get_store.cache_clear()


def test_insights_counts_tools_gaps_and_saves(
    api: TestClient, saved: FakeStore, spans: InMemorySpanExporter
) -> None:
    empty = api.get("/api/insights").json()
    assert empty == {"top_tools": [], "empty_tools": [], "requests": 0, "playlists_saved": 0}
    for body in (GITOPS, {"tools": ["argo-cd", "terraform"]}, {"tools": ["terraform"]}):
        api.post("/api/playlist", json=body)
    saved.put(store.PLAYLISTS_INDEX, "abcdefgh", {"id": "abcdefgh"})
    data = api.get("/api/insights").json()
    assert data["requests"] == 3 and data["playlists_saved"] == 1
    assert data["top_tools"][0] == {"id": "argo-cd", "name": "Argo CD", "count": 2}
    assert data["empty_tools"] == [{"id": "terraform", "name": "Terraform / OpenTofu", "count": 2}]
    span = [s for s in spans.get_finished_spans() if s.name == "api.insights"][-1]
    assert span.attributes["debrief.requests"] == 3
    assert "querylog.write" in {s.name for s in spans.get_finished_spans()}


def test_insights_backend_error_is_502(api: TestClient, saved: FakeStore) -> None:
    saved.fail = True
    assert api.get("/api/insights").status_code == 502


# --- Saved playlists --------------------------------------------------------------------


def test_save_and_load_a_playlist(
    api: TestClient, saved: FakeStore, spans: InMemorySpanExporter
) -> None:
    request = {**GITOPS, "rerank": True}
    shown = api.post("/api/playlist", json=request).json()
    assert PAIN in json.dumps(shown["debug"]["query"])
    keep = [c["clip_id"] for c in shown["clips"]][:4][::-1]
    response = api.post("/api/playlists", json={"request": request, "clip_ids": keep})
    assert response.status_code == 201, response.text
    saved_id = response.json()["id"]
    assert response.json() == {"id": saved_id, "url": f"/?p={saved_id}"}
    assert playlists.valid_id(saved_id) and len(saved_id) == 8
    doc = saved.docs["playlists"][saved_id]
    assert "pain" not in doc["request"]
    assert "query" not in doc["response"]["debug"]
    assert PAIN not in json.dumps(doc)

    loaded = api.get(f"/api/playlists/{saved_id}")
    assert loaded.status_code == 200
    data = loaded.json()
    assert set(data) == {"request", "response", "created_at"}
    assert data["request"]["tools"] == ["argo-cd", "prometheus"]
    clips = data["response"]["clips"]
    assert [c["clip_id"] for c in clips] == keep
    assert data["response"]["total_seconds"] == sum(c["end_sec"] - c["start_sec"] for c in clips)
    assert data["response"]["terms"] == shown["terms"]
    by_id = {c["clip_id"]: c for c in shown["clips"]}
    assert all(c == by_id[c["clip_id"]] for c in clips)  # the server's own clips
    names = {s.name for s in spans.get_finished_spans()}
    assert {"api.playlists.save", "api.playlists.get"} <= names
    assert PAIN not in flat_attributes(spans)


def test_save_reruns_when_the_answer_is_not_remembered(
    api: TestClient, saved: FakeStore, fake: FakeSearchClient
) -> None:
    shown = api.post("/api/playlist", json=GITOPS).json()
    playlists.recent.clear()
    saved.docs.pop("answers")  # nothing remembered anywhere
    searches = len(fake.bodies)
    keep = [shown["clips"][0]["clip_id"]]
    body = {"request": GITOPS, "clip_ids": keep, "clips": [{"quote": "browser text"}]}
    response = api.post("/api/playlists", json=body)
    assert response.status_code == 201
    assert len(fake.bodies) > searches  # ran the search again
    doc = saved.docs["playlists"][response.json()["id"]]
    assert doc["response"]["clips"][0]["quote"] == shown["clips"][0]["quote"]
    assert "browser text" not in json.dumps(doc)


def test_save_validates_clip_ids_and_body(api: TestClient) -> None:
    shown = api.post("/api/playlist", json=GITOPS).json()
    good = shown["clips"][0]["clip_id"]
    unknown = api.post("/api/playlists", json={"request": GITOPS, "clip_ids": [good, "x-1"]})
    assert unknown.status_code == 422 and "not in this playlist" in unknown.json()["detail"]
    assert api.post("/api/playlists", json={"request": GITOPS, "clip_ids": []}).status_code == 422
    too_many = {"request": GITOPS, "clip_ids": [good] * 26}
    assert api.post("/api/playlists", json=too_many).status_code == 422
    big = {"request": {**GITOPS, "attended_talk_ids": ["t" * 100] * 400}, "clip_ids": [good]}
    assert api.post("/api/playlists", json=big).status_code == 413


def test_unknown_or_malformed_playlist_ids_are_404(api: TestClient) -> None:
    assert api.get("/api/playlists/abcdefgh").status_code == 404
    assert api.get("/api/playlists/no!").status_code == 404
    assert api.get(f"/api/playlists/{'a' * 40}").status_code == 404


def test_save_retries_an_id_collision(saved: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = iter(["sameidxx", "sameidxx", "otherid1"])
    monkeypatch.setattr(playlists, "new_id", lambda: next(ids))
    assert playlists.save(saved, {}, [], {}) == "sameidxx"
    assert playlists.save(saved, {}, [], {}) == "otherid1"


def test_save_is_never_cached_and_insights_are(
    api: TestClient, saved: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEBRIEF_CACHE", "on")
    monkeypatch.setenv("DEBRIEF_CACHE_DIR", str(tmp_path))
    shown = api.post("/api/playlist", json=GITOPS).json()
    keep = [shown["clips"][0]["clip_id"]]
    response = api.post("/api/playlists", json={"request": GITOPS, "clip_ids": keep})
    assert response.status_code == 201 and demo_cache.HEADER not in response.headers
    assert saved.docs["playlists"]
    assert not list(tmp_path.glob("*.json"))  # the cache is only read here, never written

    answer = {"top_tools": [], "empty_tools": [], "requests": 41, "playlists_saved": 2}
    demo_cache.store("insights", None, answer)
    saved.fail = True  # a hit never touches Elastic
    hit = api.get("/api/insights")
    assert hit.status_code == 200 and hit.headers[demo_cache.HEADER] == "hit"
    assert hit.json()["requests"] == 41
    failed = api.post("/api/playlists", json={"request": GITOPS, "clip_ids": keep})
    assert failed.status_code == 503 and "offline demo cache" in failed.json()["detail"]


def test_cached_playlist_without_terms_is_still_valid(
    api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEBRIEF_CACHE", "on")
    monkeypatch.setenv("DEBRIEF_CACHE_DIR", str(tmp_path))
    body = routes_search.PlaylistBody.model_validate(GITOPS).model_dump(mode="json")
    live = search.build_playlist(
        FakeSearchClient(CHUNKS, TALKS), search.PlaylistRequest(**body), None
    )
    old = {k: v for k, v in live.items() if k != "terms"}
    demo_cache.store("playlist", body, old)
    data = api.post("/api/playlist", json=GITOPS).json()
    assert data["debug"]["cache"] == "hit" and data["terms"] == []
    assert not [c for c in golden.check_playlist(GITOPS, data) if not c.ok]
    keep = [data["clips"][0]["clip_id"]]
    playlists.recent.clear()  # the save loads it from the demo cache instead
    assert api.post("/api/playlists", json={"request": GITOPS, "clip_ids": keep}).status_code == 201


# --- Corrected transcripts --------------------------------------------------------------


def test_every_caption_spelling_has_a_readable_replacement() -> None:
    tools = {t.id: t for t in search.tool_catalog().values()}
    expected = {
        ("argo-cd", "argo city"): "Argo CD",
        ("argo-rollouts", "argo roll out"): "Argo Rollouts",
        ("kustomize", "customize overlay"): "Kustomize overlay",
        ("opentelemetry", "hotel collector"): "OpenTelemetry collector",
        ("linkerd", "linker d"): "Linkerd",
        ("cilium", "psyllium"): "Cilium",
        ("cert-manager", "search manager"): "cert-manager",
        ("spiffe", "spiffy id"): "SPIFFE id",
        ("karpenter", "carpenter node pools"): "Karpenter node pools",
        ("kubectl", "cube cuddle"): "kubectl",
    }
    for (tool_id, spelling), want in expected.items():
        assert corrections.replacement(spelling, tools[tool_id]) == want


def test_correct_is_whole_word_and_case_insensitive() -> None:
    tools = list(search.tool_catalog().values())
    text = "Psyllium and the hotel Collectors. Celium-based, isto. Hotel rooms. Psylliums. psyllium"
    fixed, found = corrections.correct(text, tools)
    assert fixed == (
        "Cilium and the OpenTelemetry Collectors. Cilium-based, Istio. Hotel rooms. Psylliums. "
        "Cilium"
    )  # the second "psyllium" differs only in case, so `found` lists it once
    assert found == [
        {"from": "Psyllium", "to": "Cilium"},
        {"from": "hotel Collectors", "to": "OpenTelemetry Collectors"},
        {"from": "Celium", "to": "Cilium"},
        {"from": "isto", "to": "Istio"},
    ]  # a hyphen is a word boundary (as in ingest/tag.py); "Psylliums" is another word


def test_clip_route_adds_corrected_text(api: TestClient, fake: FakeSearchClient) -> None:
    chunk_id = f"{TALK_IDS[2]}-0"
    original = "We replaced kube-proxy with psyllium. The hotel collector ships spans."
    fake.chunks[chunk_id] = {**fake.chunks[chunk_id], "text": original}
    data = api.get(f"/api/clips/{chunk_id}").json()
    assert data["text"] == original
    assert data["text_corrected"] == (
        "We replaced kube-proxy with Cilium. The OpenTelemetry collector ships spans."
    )
    assert data["corrections"] == [
        {"from": "psyllium", "to": "Cilium"},
        {"from": "hotel collector", "to": "OpenTelemetry collector"},
    ]
    clean = api.get(f"/api/clips/{TALK_IDS[1]}-0").json()
    assert clean["text_corrected"] == clean["text"] and clean["corrections"] == []


# --- Saves across replicas ----------------------------------------------------------------


class NoSearch:
    """A search client that fails the test if the save runs the playlist again."""

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("the save must reuse the shared answer, not re-run the search")

    def mget(self, index: str, ids: Any) -> list[dict[str, Any]]:
        raise AssertionError("the save must reuse the shared answer, not re-run the search")


def other_replica(monkeypatch: pytest.MonkeyPatch) -> None:
    """The save lands on another pod: empty process memory, and no re-run allowed."""
    monkeypatch.setattr(playlists, "recent", playlists.RecentAnswers())
    app.dependency_overrides[routes_search.search_client] = lambda: NoSearch()


def test_save_on_another_replica_uses_the_shared_answer(
    api: TestClient, saved: FakeStore, monkeypatch: pytest.MonkeyPatch, spans: InMemorySpanExporter
) -> None:
    body = {**GITOPS, "rerank": True}
    shown = api.post("/api/playlist", json=body).json()
    ids = [c["clip_id"] for c in shown["clips"]]
    request_hash = demo_cache.cache_key(
        "playlist", routes_search.PlaylistBody.model_validate(body).model_dump(mode="json")
    )
    assert set(saved.docs["answers"]) == {
        request_hash,
        playlists.exact_answer_id(request_hash, ids),
    }
    other_replica(monkeypatch)

    # Every clip, in another order: the exact answer, by id.
    full = api.post("/api/playlists", json={"request": body, "clip_ids": ids[::-1]})
    assert full.status_code == 201, full.text
    stored = saved.docs["playlists"][full.json()["id"]]["response"]
    by_id = {c["clip_id"]: c for c in shown["clips"]}
    assert [c["clip_id"] for c in stored["clips"]] == ids[::-1]
    assert all(c == by_id[c["clip_id"]] for c in stored["clips"])  # the server's own clips
    assert stored["terms"] == shown["terms"]

    # A subset: the latest answer for the request.
    subset = api.post("/api/playlists", json={"request": body, "clip_ids": ids[2:5]})
    assert subset.status_code == 201

    # Only an older answer left: found by search on request_hash plus every clip id.
    del saved.docs["answers"][request_hash]
    del saved.docs["answers"][playlists.exact_answer_id(request_hash, ids)]
    saved.docs["answers"]["older"] = {
        "request_hash": request_hash,
        "clip_ids": ids,
        "response": playlists.shareable(shown),
        "ts": "2026-11-09T00:00:00.000Z",
    }
    older = api.post("/api/playlists", json={"request": body, "clip_ids": ids[:1]})
    assert older.status_code == 201
    sources = [
        s.attributes["debrief.playlist_source"]
        for s in spans.get_finished_spans()
        if s.name == "api.playlists.save"
    ]
    assert sources == ["answers:exact", "answers:latest", "answers:search"]
    assert "answers.write" in {s.name for s in spans.get_finished_spans()}


def test_a_different_answer_in_memory_falls_through_to_the_shared_one(
    api: TestClient, saved: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = api.post("/api/playlist", json=GITOPS).json()
    ids = [c["clip_id"] for c in shown["clips"]]
    other_replica(monkeypatch)
    key = demo_cache.cache_key(
        "playlist", routes_search.PlaylistBody.model_validate(GITOPS).model_dump(mode="json")
    )
    playlists.recent.put(key, {**shown, "clips": shown["clips"][:1]})  # this pod's own answer
    response = api.post("/api/playlists", json={"request": GITOPS, "clip_ids": ids[-2:]})
    assert response.status_code == 201, response.text


def test_shared_answers_hold_no_pain_and_skip_empty_playlists(
    api: TestClient, saved: FakeStore
) -> None:
    api.post("/api/playlist", json={**GITOPS, "rerank": True})
    api.post("/api/playlist", json={"tools": ["a-tool-with-no-chunks"], "pain": "slow builds"})
    docs = saved.docs["answers"].values()
    assert len(docs) == 2  # exact + latest for the one non-empty playlist
    assert all("query" not in d["response"]["debug"] for d in docs)
    assert "slow" not in json.dumps(saved.docs)
    assert playlists.share_answer(None, "h", {"clips": [{"clip_id": "a-1"}]}) is False


def test_shared_answer_failures_never_break_playlist_or_save(
    api: TestClient, saved: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved.fail = True
    shown = api.post("/api/playlist", json=GITOPS)
    assert shown.status_code == 200 and shown.json()["clips"]
    assert playlists.find_shared(saved, "h", ["a-1"]) == (None, "error")
    saved.fail = False
    playlists.recent.clear()  # no memory, no shared answer: the save runs the playlist again
    ids = [shown.json()["clips"][0]["clip_id"]]
    assert api.post("/api/playlists", json={"request": GITOPS, "clip_ids": ids}).status_code == 201


def test_save_waits_briefly_for_an_answer_still_being_written(
    saved: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other replica's write lands just after its response: the second look finds it."""
    answer = {"clips": [{"clip_id": "a-1", "start_sec": 0, "end_sec": 60}]}
    real_get = saved.get
    looks: list[str] = []

    def late_get(index: str, doc_id: str) -> dict[str, Any] | None:
        looks.append(doc_id)
        if len(looks) == 3:  # the answer lands after the first round of lookups
            playlists.share_answer(saved, "h", answer)
        return real_get(index, doc_id)

    monkeypatch.setattr(saved, "get", late_get)
    monkeypatch.setattr(playlists, "SHARED_RETRY_DELAYS", (0.0, 0.0))
    found, how = playlists.wait_for_shared(saved, "h", ["a-1"])
    assert found == answer and how == "exact"
    assert playlists.wait_for_shared(None, "h", ["a-1"]) == (None, "none")


def test_index_warm_up_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []
    fake_store = FakeStore()
    monkeypatch.setattr(fake_store, "ensure_index", created.append)
    monkeypatch.setattr(store, "get_store", lambda: fake_store)
    store.warm()
    for _ in range(100):
        if len(created) == 3:
            break
        time.sleep(0.01)
    assert sorted(created) == ["answers", "playlists", "queries"]

    def broken() -> store.StoreClient:
        raise SearchBackendError("ELASTIC_URL and ELASTIC_API_KEY must be set")

    monkeypatch.setattr(store, "get_store", broken)
    store.warm()  # logs and returns
