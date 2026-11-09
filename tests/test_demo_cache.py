"""Task 13: the offline demo cache (api/demo_cache.py), against fakes and a temp directory."""

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api import demo_cache, routes_memo, routes_search, routes_stack, search
from api.main import app
from api.routes_search import PlaylistBody
from tests import build_cache, golden
from tests.test_rerank_memo import FakeLLM, memo_reply, prompt_clips
from tests.test_search import CHUNKS, TALK_IDS, TALKS, FakeSearchClient

GITOPS = {"tools": ["argo-cd", "prometheus"], "pain": "our Argo CD syncs are slow", "rerank": False}


class Untouchable:
    """A search client and model that fail the test if a cached request reaches them."""

    calls = 0

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        Untouchable.calls += 1
        raise AssertionError("a cache hit must not call Elastic")

    def mget(self, index: str, ids: Sequence[str]) -> list[dict[str, Any]]:
        Untouchable.calls += 1
        raise AssertionError("a cache hit must not call Elastic")

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        Untouchable.calls += 1
        raise AssertionError("a cache hit must not call the model")


class Offline:
    """Elastic with the network off."""

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        raise search.SearchBackendError("Elastic search failed (ConnectionError)")

    def mget(self, index: str, ids: Sequence[str]) -> list[dict[str, Any]]:
        raise search.SearchBackendError("Elastic mget failed (ConnectionError)")


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEBRIEF_CACHE", "on")
    monkeypatch.setenv("DEBRIEF_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def use(search_client: Any, model: Any = None) -> None:
    app.dependency_overrides[routes_search.search_client] = lambda: search_client
    app.dependency_overrides[routes_search.model_client] = lambda: model or Untouchable()


def live_playlist(body: dict[str, Any]) -> dict[str, Any]:
    request = search.PlaylistRequest(**PlaylistBody.model_validate(body).model_dump())
    result = search.build_playlist(FakeSearchClient(CHUNKS, TALKS), request, None)
    return {**result, "trace_id": None}


# --- The key ----------------------------------------------------------------------------


def test_key_ignores_key_order_tool_order_and_repeats() -> None:
    a = {"tools": ["argo-cd", "prometheus"], "pain": "slow", "rerank": False}
    b = {"rerank": False, "pain": "slow", "tools": ["prometheus", "argo-cd", "argo-cd"]}
    assert demo_cache.cache_key("playlist", a) == demo_cache.cache_key("playlist", b)
    memo_a = {"tools": ["b", "a"], "clip_ids": ["x-1", "y-2"]}
    memo_b = {"clip_ids": ["y-2", "x-1"], "tools": ["a", "b"]}
    assert demo_cache.cache_key("memo", memo_a) == demo_cache.cache_key("memo", memo_b)


def test_key_is_stable_across_runs_and_releases() -> None:
    # Pinned: changing the key format orphans the committed cache, so rebuild it if this moves.
    body = PlaylistBody.model_validate({"tools": ["prometheus", "argo-cd"]}).model_dump()
    assert demo_cache.cache_key("playlist", body) == (
        "275be3093c804d93b4d04ebb214da6744648077646982a5b24ef7f2634b62b2d"
    )


def test_key_separates_what_changes_the_answer() -> None:
    base = PlaylistBody.model_validate(GITOPS).model_dump()
    keys = {
        demo_cache.cache_key("playlist", base),
        demo_cache.cache_key("playlist", {**base, "rerank": True}),
        demo_cache.cache_key("playlist", {**base, "filter": "missed"}),
        demo_cache.cache_key("playlist", {**base, "tools": ["argo-cd"]}),
        demo_cache.cache_key("memo", base),
    }
    assert len(keys) == 5


def test_defaults_and_blank_pain_share_a_key() -> None:
    sparse = PlaylistBody.model_validate({"tools": ["istio"]}).model_dump(mode="json")
    full = PlaylistBody.model_validate(
        {
            "tools": ["istio"],
            "pain": "  ",
            "attended_talk_ids": [],
            "filter": "all",
            "budget_minutes": 25,
            "rerank": True,
        }
    ).model_dump(mode="json")
    assert demo_cache.cache_key("playlist", sparse) == demo_cache.cache_key("playlist", full)


def test_unreadable_or_mismatched_files_are_misses(cache_dir: Path) -> None:
    body = {"tools": ["istio"]}
    path = demo_cache.cache_path("playlist", body)
    path.write_text("{not json", encoding="utf-8")
    assert demo_cache.lookup("playlist", body) is None
    path.write_text(json.dumps({"request": {"tools": ["envoy"]}, "response": {}}))
    assert demo_cache.lookup("playlist", body) is None


# --- Hits, misses and the flag ----------------------------------------------------------


def test_playlist_hit_touches_neither_elastic_nor_the_model(
    cache_dir: Path, client: TestClient
) -> None:
    body = PlaylistBody.model_validate(GITOPS).model_dump(mode="json")
    stored = live_playlist(GITOPS)
    demo_cache.store("playlist", body, stored)
    use(Untouchable())
    Untouchable.calls = 0
    # Same request, other key order and tool order: still a hit.
    response = client.post(
        "/api/playlist", json={"rerank": False, **GITOPS, "tools": ["prometheus", "argo-cd"]}
    )
    assert response.status_code == 200, response.text
    assert response.headers[demo_cache.HEADER] == "hit"
    data = response.json()
    assert data["debug"]["cache"] == "hit"
    assert [c["clip_id"] for c in data["clips"]] == [c["clip_id"] for c in stored["clips"]]
    assert data["trace_id"] and len(data["trace_id"]) == 32
    assert Untouchable.calls == 0
    failed = [c for c in golden.check_playlist(GITOPS, data) if not c.ok]
    assert not failed and golden.cache_check(data).ok


def test_miss_falls_through_to_the_live_path(cache_dir: Path, client: TestClient) -> None:
    use(FakeSearchClient(CHUNKS, TALKS))
    response = client.post("/api/playlist", json=GITOPS)
    assert response.status_code == 200
    assert response.headers[demo_cache.HEADER] == "miss"
    assert "cache" not in response.json()["debug"]
    assert response.json()["clips"]


def test_miss_with_the_network_off_is_a_clear_503(cache_dir: Path, client: TestClient) -> None:
    use(demo_cache.OfflineSearchClient())
    for method, path, body in (
        ("post", "/api/playlist", GITOPS),
        ("post", "/api/memo", {"tools": ["argo-cd"], "clip_ids": ["talka0000-0"]}),
        ("get", "/api/tools", None),
        ("get", "/api/talks", None),
        ("get", "/api/clips/talka0000-0", None),
    ):
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 503, (path, response.text)
        assert "offline demo cache" in response.json()["detail"]
        assert response.headers[demo_cache.HEADER] == "miss"


def test_elastic_errors_are_not_offline_cache_misses(cache_dir: Path, client: TestClient) -> None:
    use(Offline())
    for method, path, body in (
        ("post", "/api/playlist", GITOPS),
        ("post", "/api/memo", {"tools": ["argo-cd"], "clip_ids": ["talka0000-0"]}),
        ("get", "/api/tools", None),
        ("get", "/api/talks", None),
        ("get", "/api/clips/talka0000-0", None),
    ):
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 502, (path, response.text)
        assert "offline demo cache" not in response.json()["detail"]


def test_cache_on_without_elastic_config_still_serves_hits(
    cache_dir: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ELASTIC_URL", raising=False)
    monkeypatch.delenv("ELASTIC_API_KEY", raising=False)
    search.get_client.cache_clear()
    demo_cache.store("tools", None, [{"id": "argo-cd", "name": "Argo CD", "chunk_count": 9}])
    try:
        hit = client.get("/api/tools")
        assert hit.status_code == 200 and hit.headers[demo_cache.HEADER] == "hit"
        miss = client.get("/api/talks")
        assert miss.status_code == 503 and "not configured" in miss.json()["detail"]
    finally:
        search.get_client.cache_clear()


def test_flag_off_is_a_no_op(
    cache_dir: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = PlaylistBody.model_validate(GITOPS).model_dump(mode="json")
    demo_cache.store("playlist", body, {"clips": [], "total_seconds": 0, "debug": {}})
    monkeypatch.setenv("DEBRIEF_CACHE", "off")

    def no_lookup(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the cache must not be read with the flag off")

    monkeypatch.setattr(demo_cache, "lookup", no_lookup)
    use(FakeSearchClient(CHUNKS, TALKS))
    response = client.post("/api/playlist", json=GITOPS)
    assert response.status_code == 200
    assert demo_cache.HEADER not in response.headers
    assert "cache" not in response.json()["debug"]
    assert response.json()["clips"]  # the live answer, not the stored empty one
    use(Offline())
    assert client.post("/api/playlist", json=GITOPS).status_code == 502  # unchanged


def test_memo_stack_tools_talks_and_clips_hits(cache_dir: Path, client: TestClient) -> None:
    use(Untouchable())
    memo = {"tools": ["argo-cd"], "clip_ids": ["talka0000-0", "talka0001-60"]}
    stack = {
        "tools": [],
        "dependency_file": {"name": "go.mod", "content": "require istio.io/client-go v1"},
    }
    stack_answer = {
        "tools": [{"id": "istio", "name": "Istio", "source": "go.mod"}],
        "unknown_packages": [],
    }
    clip = {"clip_id": "talka0000-0~60", "talk_id": "talka0000", "text": "..."}
    demo_cache.store("memo", memo, {"markdown": "# Memo", "cited_clip_ids": ["talka0000-0"]})
    demo_cache.store("stack", stack, stack_answer)
    demo_cache.store("talks", None, [{"talk_id": "talka0000", "title": "T", "speakers": []}])
    demo_cache.store("tools", None, [{"id": "istio", "name": "Istio", "chunk_count": 7}])
    demo_cache.store("clips", {"clip_id": clip["clip_id"]}, clip)

    reversed_memo = {"clip_ids": memo["clip_ids"][::-1], "tools": memo["tools"]}
    for response, expected in (
        (client.post("/api/memo", json=reversed_memo), "# Memo"),
        (client.post("/api/stack", json={"dependency_file": stack["dependency_file"]}), "istio"),
        (client.get("/api/talks"), "talka0000"),
        (client.get("/api/tools"), "istio"),
        (client.get("/api/clips/talka0000-0~60"), "talka0000-0~60"),
    ):
        assert response.status_code == 200, response.text
        assert response.headers[demo_cache.HEADER] == "hit"
        assert expected in response.text


def test_stack_miss_is_parsed_live_offline(cache_dir: Path, client: TestClient) -> None:
    response = client.post("/api/stack", json={"tools": ["istio"]})
    assert response.status_code == 200
    assert response.headers[demo_cache.HEADER] == "miss"
    assert response.json()["tools"][0]["id"] == "istio"


# --- Spans --------------------------------------------------------------------------------


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("debrief")
    for module in (search, routes_search, routes_memo, routes_stack):
        monkeypatch.setattr(module, "tracer", tracer)
    return exporter


def route_span(exporter: InMemorySpanExporter, name: str) -> dict[str, Any]:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == name]
    return dict(span.attributes or {})


def test_route_spans_carry_cache_hit(
    cache_dir: Path, client: TestClient, spans: InMemorySpanExporter
) -> None:
    demo_cache.store("tools", None, [])
    use(FakeSearchClient(CHUNKS, TALKS))
    client.get("/api/tools")
    client.get("/api/talks")
    assert route_span(spans, "api.tools")[demo_cache.SPAN_ATTRIBUTE] is True
    assert route_span(spans, "api.talks")[demo_cache.SPAN_ATTRIBUTE] is False


def test_flag_off_spans_have_no_cache_attribute(
    client: TestClient, spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEBRIEF_CACHE", raising=False)
    use(FakeSearchClient(CHUNKS, TALKS))
    client.get("/api/tools")
    assert demo_cache.SPAN_ATTRIBUTE not in route_span(spans, "api.tools")


# --- The builder, end to end against the fake-backed app ---------------------------------


class AppApi:
    """`build_cache.Api` over the TestClient, so the builder runs without a network."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def get(self, path: str) -> Any:
        response = self.client.get(path)
        if response.status_code != 200:
            raise build_cache.ApiError(f"{path} -> HTTP {response.status_code}")
        return response.json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        response = self.client.post(path, json=body)
        if response.status_code != 200:
            raise build_cache.ApiError(f"{path} -> HTTP {response.status_code}")
        return response.json()


def test_built_cache_passes_golden_offline(
    tmp_path: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stacks = [
        {**s, "attended_talk_ids": [TALK_IDS[0]]} if s.get("attended_talk_ids") else s
        for s in golden.load_stacks()
    ]
    go_mod = (Path(__file__).parent / "fixtures" / "go.mod").read_text(encoding="utf-8")

    # 1. Build from the live (fake-backed) API with the cache off.
    monkeypatch.delenv("DEBRIEF_CACHE", raising=False)
    use(
        FakeSearchClient(CHUNKS, TALKS),
        FakeLLM(lambda p: memo_reply([c["clip_id"] for c in prompt_clips(p)])),
    )
    lines: list[str] = []
    summary = build_cache.build(AppApi(client), tmp_path, stacks, go_mod, out=lines.append)
    assert summary.ok(), summary.failures + summary.secrets
    routes = sorted(set(summary.written.values()))
    assert routes == ["clips", "memo", "playlist", "stack", "talks", "tools"]

    # 2. Network off, cache on: every golden stack passes, with and without rerank.
    monkeypatch.setenv("DEBRIEF_CACHE", "on")
    monkeypatch.setenv("DEBRIEF_CACHE_DIR", str(tmp_path))
    use(Offline())

    def fetch(body: dict[str, Any]) -> dict[str, Any]:
        response = client.post("/api/playlist", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    for rerank in (False, True):
        out: list[str] = []
        assert golden.run(stacks, fetch, out=out.append, rerank=rerank, cache=True) == 0, out


def test_secret_scan_flags_keys_not_ordinary_words() -> None:
    assert build_cache.find_secrets('"Authorization": "Bearer abcdefgh12345678"')
    assert build_cache.find_secrets("key sk-ant-api03-abcdefghijklmnop")
    assert build_cache.find_secrets("ApiKey bm90LWEtcmVhbC1rZXk=")
    assert not build_cache.find_secrets("a task-runner for risk-based Argo CD syncs")
