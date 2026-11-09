"""Structured event logs, access logs and product metrics (telemetry enrichment).

Each event is one INFO (or WARN) log with `event.name` and `debrief.*` attributes: ids,
counts, durations and fixed enums only. Everything runs in memory, with fakes for Elastic,
the store and the model.
"""

import json
import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from api import demo_cache, playlists, rerank, routes_playlists, routes_search, telemetry
from api.llm import LLMError
from api.main import app
from tests.test_playlists_insights import FakeStore
from tests.test_rerank_memo import NAMES, FakeLLM, memo_reply, scored_reply, synthetic
from tests.test_search import CHUNKS, TALKS, FakeSearchClient

PAIN = "our Argo CD syncs are slow and nobody knows why"
GITOPS = {"tools": ["argo-cd", "prometheus"], "pain": PAIN}
# Text that must never reach a log: the pain sentence, transcript words, memo lines.
TEXT_BITS = (PAIN, "production", "platform work", "We measured", "Shard the", "Invented")
LABELS = {"deep_dive", "intro", "pitch", "unlabelled"}


@pytest.fixture
def logs() -> Iterator[InMemoryLogRecordExporter]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = telemetry.otel_log_handler(provider)
    logger = logging.getLogger("debrief")
    logger.addHandler(handler)
    yield exporter
    logger.removeHandler(handler)


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("debrief")
    monkeypatch.setattr(telemetry, "instruments", telemetry.Instruments(meter))
    return reader


@pytest.fixture
def saved() -> FakeStore:
    return FakeStore()


@pytest.fixture
def api(saved: FakeStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fake = FakeSearchClient(CHUNKS, TALKS)
    monkeypatch.setattr(playlists, "SHARED_RETRY_DELAYS", (0.0,))
    playlists.recent.clear()
    app.dependency_overrides[routes_search.search_client] = lambda: fake
    app.dependency_overrides[routes_playlists.playlist_store] = lambda: saved
    app.dependency_overrides[routes_search.query_log_store] = lambda: saved
    yield TestClient(app)
    app.dependency_overrides.clear()
    playlists.recent.clear()


def events(exporter: InMemoryLogRecordExporter, name: str) -> list[Any]:
    return [
        r.log_record
        for r in exporter.get_finished_logs()
        if (r.log_record.attributes or {}).get("event.name") == name
    ]


def fields(record: Any) -> dict[str, Any]:
    """The record's `debrief.*` attributes without the prefix; tuples become lists."""
    return {
        k.removeprefix("debrief."): list(v) if isinstance(v, tuple) else v
        for k, v in (record.attributes or {}).items()
        if k.startswith("debrief.")
    }


def assert_no_text(record: Any) -> None:
    flat = f"{record.body} {json.dumps(fields(record), default=str)}"
    assert not any(bit in flat for bit in TEXT_BITS), flat


def points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    return [
        p
        for rm in (data.resource_metrics if data else [])
        for sm in rm.scope_metrics
        for m in sm.metrics
        if m.name == name
        for p in m.data.data_points
    ]


def counts(reader: InMemoryMetricReader, name: str, *keys: str) -> dict[Any, int]:
    out: dict[Any, int] = {}
    for p in points(reader, name):
        key = tuple(p.attributes[k] for k in keys)
        out[key[0] if len(keys) == 1 else key] = p.value
    return out


# --- playlist.served, tool requests, clip labels ------------------------------------------


def test_playlist_served_event_and_product_counters(
    api: TestClient, logs: InMemoryLogRecordExporter, reader: InMemoryMetricReader
) -> None:
    body = {**GITOPS, "tools": [*GITOPS["tools"], "not-a-real-tool"], "rerank": False}
    data = api.post("/api/playlist", json=body).json()
    assert data["clips"]

    (record,) = events(logs, "playlist.served")
    assert record.severity_text == "INFO"
    assert str(record.body).startswith("event=playlist.served tools=argo-cd,prometheus,other ")
    assert format(record.trace_id, "032x") == data["trace_id"]
    got = fields(record)
    assert got["tools"] == ["argo-cd", "prometheus", "other"]
    assert got["clip_count"] == len(data["clips"])
    assert got["talk_count"] == len({c["talk_id"] for c in data["clips"]})
    assert got["total_seconds"] == data["total_seconds"]
    assert (got["filter"], got["rerank"], got["cache"]) == ("all", False, "off")
    assert (got["elastic_rerank"], got["model_rerank"]) == ("off", "off")
    assert got["duration_ms"] >= 0
    assert_no_text(record)

    tools = counts(reader, "debrief.playlist.tool_requests", "tool")
    assert tools == {"argo-cd": 1, "prometheus": 1, "other": 1}
    labels = counts(reader, "debrief.playlist.clip_labels", "label")
    assert set(labels) <= LABELS
    assert sum(labels.values()) == len(data["clips"])


def test_empty_playlist_is_served_too(
    api: TestClient, logs: InMemoryLogRecordExporter, reader: InMemoryMetricReader
) -> None:
    api.post("/api/playlist", json={"tools": ["kafka"], "pain": PAIN})
    (record,) = events(logs, "playlist.served")
    assert fields(record)["clip_count"] == 0
    assert counts(reader, "debrief.playlist.tool_requests", "tool") == {"kafka": 1}
    assert points(reader, "debrief.playlist.clip_labels") == []


# --- Rerank: one log per batch, a counter by outcome, WARN on fallback ----------------------


def test_rerank_batch_events_and_outcome_counter(
    logs: InMemoryLogRecordExporter, reader: InMemoryMetricReader
) -> None:
    records, clips = synthetic()
    rerank.judge(FakeLLM(scored_reply()), records, clips, ["argo-cd"], NAMES, {}, PAIN)
    with pytest.raises(LLMError):
        rerank.judge(FakeLLM(LLMError("down")), records, clips, ["argo-cd"], NAMES, {}, PAIN)

    batches = events(logs, "rerank.batch")
    assert len(batches) == 6
    ok = [fields(r) for r in batches if r.severity_text == "INFO"]
    failed = [fields(r) for r in batches if r.severity_text == "WARN"]
    assert sorted(f["index"] for f in ok) == [0, 1, 2]
    assert all(f["size"] == 6 and f["outcome"] == "ok" and "error" not in f for f in ok)
    assert all(f["outcome"] == "fallback" and f["error"] == "LLMError" for f in failed)
    assert len(failed) == 3
    for record in batches:
        assert_no_text(record)
    assert counts(reader, "debrief.rerank.batches", "outcome") == {"ok": 3, "fallback": 3}


class FirstCallFails(FakeLLM):
    """The first rerank call raises LLMError; the rest answer."""

    def __init__(self) -> None:
        super().__init__(scored_reply())
        self.lock = threading.Lock()
        self.failed = False

    def complete(self, prompt: str, **kw: Any) -> Any:
        with self.lock:
            fail, self.failed = not self.failed, True
        if fail:
            raise LLMError("first call fails")
        return super().complete(prompt, **kw)


def test_partial_and_full_model_fallbacks_log_warnings(
    api: TestClient, logs: InMemoryLogRecordExporter
) -> None:
    app.dependency_overrides[routes_search.model_client] = FirstCallFails
    data = api.post("/api/playlist", json={**GITOPS, "rerank": True}).json()
    status = data["debug"]["rerank"]["model"]
    assert status.startswith("partial: 1/")
    (record,) = events(logs, "rerank.partial")
    assert record.severity_text == "WARN"
    assert fields(record)["failed"] == 1 and fields(record)["outcomes"] == ["fallback"]
    served = fields(events(logs, "playlist.served")[0])
    assert served["model_rerank"] == status and served["rerank"] is True

    app.dependency_overrides[routes_search.model_client] = lambda: FakeLLM(LLMError("down"))
    api.post("/api/playlist", json={**GITOPS, "rerank": True})
    (record,) = events(logs, "rerank.model_fallback")
    assert record.severity_text == "WARN"
    assert fields(record)["error"] == "LLMError" and fields(record)["clips_sent"] > 0


# --- memo.made and the memo latency histogram ---------------------------------------------


def test_memo_made_event_and_duration(
    api: TestClient, logs: InMemoryLogRecordExporter, reader: InMemoryMetricReader
) -> None:
    shown = api.post("/api/playlist", json={**GITOPS, "rerank": False}).json()
    ids = [c["clip_id"] for c in shown["clips"][:8]]
    app.dependency_overrides[routes_search.model_client] = lambda: FakeLLM(memo_reply(ids))
    data = api.post("/api/memo", json={"tools": GITOPS["tools"], "clip_ids": ids}).json()

    (record,) = events(logs, "memo.made")
    assert record.severity_text == "INFO"
    got = fields(record)
    assert got["clip_count"] == len(ids)
    assert got["cited_count"] == len(data["cited_clip_ids"])
    assert got["dropped_lines"] == 4  # the invented, uncited and unknown-id lines
    assert (got["tokens"], got["input_tokens"], got["output_tokens"]) == (180, 120, 60)
    assert got["tools"] == ["argo-cd", "prometheus"] and got["cache"] == "off"
    assert got["duration_ms"] >= 0
    assert_no_text(record)
    (duration,) = points(reader, "debrief.memo.duration")
    assert duration.count == 1 and duration.attributes == {"outcome": "ok"}


def test_memo_failure_logs_a_warning_and_records_an_error(
    api: TestClient, logs: InMemoryLogRecordExporter, reader: InMemoryMetricReader
) -> None:
    shown = api.post("/api/playlist", json={**GITOPS, "rerank": False}).json()
    ids = [c["clip_id"] for c in shown["clips"][:3]]
    app.dependency_overrides[routes_search.model_client] = lambda: FakeLLM(LLMError("down"))
    assert api.post("/api/memo", json={"clip_ids": ids}).status_code == 502
    (record,) = events(logs, "memo.failed")
    assert record.severity_text == "WARN" and fields(record)["error"] == "LLMError"
    (duration,) = points(reader, "debrief.memo.duration")
    assert duration.attributes == {"outcome": "error"}


# --- stack.parsed ---------------------------------------------------------------------------


def test_stack_parsed_event_has_counts_not_package_names(
    api: TestClient, logs: InMemoryLogRecordExporter
) -> None:
    content = (
        "module example.com/x\n\nrequire (\n"
        "\tgithub.com/argoproj/argo-cd/v2 v2.10.0\n"
        "\tgithub.com/acme/secret-internal-lib v1.0.0\n)\n"
    )
    response = api.post(
        "/api/stack", json={"tools": [], "dependency_file": {"name": "go.mod", "content": content}}
    )
    assert response.status_code == 200, response.text
    (record,) = events(logs, "stack.parsed")
    got = fields(record)
    assert got["file_type"] == "go"
    assert got["package_count"] == 2
    assert got["matched_tools"] == len(got["tools"]) == len(response.json()["tools"])
    assert got["unknown_count"] == len(response.json()["unknown_packages"]) >= 1
    assert "secret-internal-lib" not in f"{record.body} {record.attributes}"


# --- playlist.shared, playlist.loaded, insights.read, cache lookups ----------------------


def test_share_load_and_insights_events(
    api: TestClient, logs: InMemoryLogRecordExporter, reader: InMemoryMetricReader
) -> None:
    request = {**GITOPS, "rerank": False}
    shown = api.post("/api/playlist", json=request).json()
    keep = [c["clip_id"] for c in shown["clips"][:3]]
    saved_id = api.post("/api/playlists", json={"request": request, "clip_ids": keep}).json()["id"]
    assert api.get(f"/api/playlists/{saved_id}").status_code == 200
    assert api.get("/api/playlists/zzzzzzzz").status_code == 404
    assert api.get("/api/insights").status_code == 200

    (shared,) = events(logs, "playlist.shared")
    assert fields(shared) | {"source": "x"} == {
        "id": saved_id,
        "clip_count": 3,
        "source": "x",
        "filter": "all",
    }
    loaded = [fields(r) for r in events(logs, "playlist.loaded")]
    assert loaded == [
        {"id": saved_id, "found": True, "clip_count": 3},
        {"id": "zzzzzzzz", "found": False, "clip_count": 0},
    ]
    (insights,) = events(logs, "insights.read")
    assert fields(insights)["cache"] == "off" and fields(insights)["requests"] >= 0
    for record in logs.get_finished_logs():
        assert PAIN not in f"{record.log_record.body} {record.log_record.attributes}"
    (count,) = points(reader, "debrief.playlists.shared")
    assert count.value == 1


def test_cache_lookups_are_counted_only_when_the_cache_is_on(
    api: TestClient, reader: InMemoryMetricReader, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.get("/api/tools")
    assert points(reader, "debrief.cache.lookups") == []  # cache off: nothing recorded
    monkeypatch.setenv("DEBRIEF_CACHE", "on")
    monkeypatch.setenv("DEBRIEF_CACHE_DIR", str(tmp_path))
    demo_cache.store("tools", None, [])
    api.get("/api/tools")
    api.get("/api/talks")
    got = counts(reader, "debrief.cache.lookups", "route", "result")
    assert got == {("tools", "hit"): 1, ("talks", "miss"): 1}


# --- Access logs and HTTP metrics -------------------------------------------------------------


def uvicorn_line(logger: logging.Logger, path: str, status: int = 200) -> None:
    """What uvicorn's access logger emits for one request."""
    logger.info('%s - "%s %s HTTP/%s" %d', "10.0.0.1:5000", "GET", path, "1.1", status)


def test_access_log_reaches_otel_with_the_trace_id_and_skips_probes() -> None:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = telemetry.otel_log_handler(provider)
    access = logging.getLogger(telemetry.ACCESS_LOGGER)
    telemetry.attach_access_log(handler)
    try:
        with trace.get_tracer("test").start_as_current_span("GET /api/version") as span:
            uvicorn_line(access, "/api/version")
            uvicorn_line(access, "/healthz")
            uvicorn_line(access, "/readyz?full=1")
            trace_id = span.get_span_context().trace_id
    finally:
        access.removeHandler(handler)
    (record,) = [r.log_record for r in exporter.get_finished_logs()]
    assert record.body == '10.0.0.1:5000 - "GET /api/version HTTP/1.1" 200'
    assert record.trace_id == trace_id


def test_fastapi_records_http_duration_and_active_requests_without_probes() -> None:
    reader = InMemoryMetricReader()
    small = FastAPI()

    @small.get("/api/things/{thing_id}")
    def thing(thing_id: str) -> dict[str, str]:
        return {"id": thing_id}

    @small.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    telemetry.instrument_app(small, MeterProvider(metric_readers=[reader]))
    client = TestClient(small)
    client.get("/api/things/1")
    client.get("/api/things/2")
    client.get("/healthz")
    (duration,) = points(reader, "http.server.duration")
    assert duration.count == 2
    assert duration.attributes["http.target"] == "/api/things/{thing_id}"
    assert duration.attributes["http.status_code"] == 200
    assert points(reader, "http.server.active_requests")
