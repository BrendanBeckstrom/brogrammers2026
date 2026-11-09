"""Task 11: one request is one trace, trace ids reach the client, metrics and logs carry no text.

Everything runs in memory: the global tracer provider that `api.main` installs gets an
in-memory span exporter, metrics go to an in-memory reader, logs to an in-memory exporter.
"""

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api import routes_search, telemetry
from api.main import app
from ingest import index as idx
from tests.test_index import FakeClient, _chunk, _write_chunks
from tests.test_search import CHUNKS, TALKS, FakeSearchClient

PAIN = "our Argo CD syncs are slow and nobody knows why"
# Words that appear only in transcript text of the fake corpus.
TEXT_BITS = (PAIN, "production", "platform work", "We measured")

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def _global_exporter() -> None:
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), "api.main should have called telemetry.setup()"
    provider.add_span_processor(SimpleSpanProcessor(_exporter))


@pytest.fixture
def spans() -> InMemorySpanExporter:
    _exporter.clear()
    return _exporter


@pytest.fixture
def api() -> Iterator[TestClient]:
    fake = FakeSearchClient(CHUNKS, TALKS)
    app.dependency_overrides[routes_search.search_client] = lambda: fake
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("debrief")
    monkeypatch.setattr(telemetry, "instruments", telemetry.Instruments(meter))
    return reader


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


def points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out.setdefault(metric.name, []).extend(metric.data.data_points)
    return out


def assert_no_text(finished: tuple[ReadableSpan, ...]) -> None:
    for span in finished:
        for value in (span.attributes or {}).values():
            flat = " ".join(map(str, value)) if isinstance(value, tuple) else str(value)
            assert not any(bit in flat for bit in TEXT_BITS), (span.name, value)


# --- Traces ----------------------------------------------------------------------------


def test_playlist_is_one_trace_under_one_root(api: TestClient, spans: InMemorySpanExporter) -> None:
    response = api.post("/api/playlist", json={"tools": ["argo-cd", "prometheus"], "pain": PAIN})
    assert response.status_code == 200
    finished = spans.get_finished_spans()

    assert len({s.context.trace_id for s in finished}) == 1  # one trace
    roots = [s for s in finished if s.parent is None]
    assert len(roots) == 1
    root = roots[0]
    assert root.name == "POST /api/playlist"
    assert len(finished) >= 4
    by_id = {s.context.span_id: s for s in finished}
    for span in finished:  # every span hangs off the root
        node = span
        while node.parent is not None:
            node = by_id[node.parent.span_id]
        assert node is root, span.name
    names = {s.name for s in finished}
    assert {"api.playlist", "search.hybrid", "search.keyword", "search.semantic"} <= names
    assert by_id[next(s for s in finished if s.name == "api.playlist").parent.span_id] is root

    root_trace_id = format(root.context.trace_id, "032x")
    assert response.json()["trace_id"] == root_trace_id
    assert response.headers["x-trace-id"] == root_trace_id
    assert_no_text(finished)


def test_browser_traceparent_links_the_trace(api: TestClient, spans: InMemorySpanExporter) -> None:
    trace_id, parent_id = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
    response = api.post(
        "/api/playlist",
        json={"tools": ["istio"]},
        headers={"traceparent": f"00-{trace_id}-{parent_id}-01"},
    )
    assert response.json()["trace_id"] == trace_id
    assert response.headers["x-trace-id"] == trace_id
    server = next(s for s in spans.get_finished_spans() if s.name == "POST /api/playlist")
    assert server.parent is not None and server.parent.is_remote
    assert format(server.parent.span_id, "016x") == parent_id


def test_every_api_response_has_a_trace_id_header(api: TestClient) -> None:
    for path in ("/api/version", "/api/tools", "/api/talks"):
        assert len(api.get(path).headers["x-trace-id"]) == 32


def test_probes_are_not_traced(api: TestClient, spans: InMemorySpanExporter) -> None:
    for path in ("/healthz", "/readyz"):
        response = api.get(path)
        assert response.status_code == 200
        assert "x-trace-id" not in response.headers
    assert spans.get_finished_spans() == ()


def test_ingest_run_is_one_trace_with_a_span_per_batch(
    tmp_path: Path, spans: InMemorySpanExporter
) -> None:
    chunks = [_chunk("t1", s) for s in (0, 60, 120)] + [_chunk("t2", s) for s in (0, 60)]
    path = _write_chunks(tmp_path / "chunks.jsonl", chunks)
    assert idx.run(FakeClient(), path, batch_size=2, progress=None) == 0
    finished = spans.get_finished_spans()
    root = next(s for s in finished if s.name == "ingest.index")
    batches = [s for s in finished if s.name == "ingest.bulk_batch"]
    assert root.parent is None
    assert len(batches) == 1 + 3  # talks: one batch of 2; clips: 2 + 2 + 1
    assert all(b.parent and b.parent.span_id == root.context.span_id for b in batches)
    assert root.attributes["debrief.chunks"] == 5
    assert_no_text(finished)


# --- Metrics ---------------------------------------------------------------------------


def test_playlist_metrics(api: TestClient, reader: InMemoryMetricReader) -> None:
    api.post("/api/playlist", json={"tools": ["argo-cd"]})
    api.post("/api/playlist", json={"tools": ["kafka", "not-a-real-tool"], "filter": "all"})
    got = points(reader)

    durations = got["debrief.playlist.duration"]
    assert sum(p.count for p in durations) == 2
    assert {p.attributes["outcome"] for p in durations} == {"ok", "empty"}
    clips = got["debrief.playlist.clips"]
    assert sum(p.count for p in clips) == 2 and sum(p.sum for p in clips) > 0
    empty = {p.attributes["tool"]: p.value for p in got["debrief.playlist.empty"]}
    assert empty == {"kafka": 1, "other": 1}  # unknown ids never become attribute values


def test_llm_and_memo_helpers(reader: InMemoryMetricReader) -> None:
    telemetry.record_llm_tokens("rerank", "anthropic", input_tokens=1200, output_tokens=300)
    telemetry.record_llm_tokens("memo", "anthropic", input_tokens=900, output_tokens=0)
    telemetry.record_memo_line_dropped("unknown_clip", "abc123-60")
    telemetry.record_memo_line_dropped("uncited")
    got = points(reader)
    tokens = {
        (p.attributes["purpose"], p.attributes["token_type"]): p.value
        for p in got["debrief.llm.tokens"]
    }
    assert tokens == {("rerank", "input"): 1200, ("rerank", "output"): 300, ("memo", "input"): 900}
    dropped = {p.attributes["reason"]: p.value for p in got["debrief.memo.citations_dropped"]}
    assert dropped == {"unknown_clip": 1, "uncited": 1}


# --- Logs ------------------------------------------------------------------------------


def test_empty_playlist_log_has_trace_id_and_no_text(
    api: TestClient, logs: InMemoryLogRecordExporter
) -> None:
    data = api.post("/api/playlist", json={"tools": ["kafka"], "pain": PAIN}).json()
    assert data["clips"] == []
    records = [
        r.log_record for r in logs.get_finished_logs() if r.log_record.body == "empty playlist"
    ]
    assert len(records) == 1
    record = records[0]
    assert format(record.trace_id, "032x") == data["trace_id"]
    assert tuple(record.attributes["debrief.tools"]) == ("kafka",)
    assert not any(bit in str(dict(record.attributes)) for bit in TEXT_BITS)


def test_memo_line_dropped_log(logs: InMemoryLogRecordExporter) -> None:
    telemetry.record_memo_line_dropped("unknown_clip", "zzz-1")
    (record,) = [r.log_record for r in logs.get_finished_logs()]
    assert record.body == "memo line dropped"
    assert record.attributes["debrief.cited_clip_id"] == "zzz-1"


# --- Resource ----------------------------------------------------------------------------


def test_resource_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "abc1234")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "debrief-api-test")
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    api_attrs = telemetry.build_resource(telemetry.API_SERVICE).attributes
    assert api_attrs["service.name"] == "debrief-api-test"
    assert api_attrs["service.version"] == "abc1234"
    assert api_attrs["deployment.environment"] == "local"
    assert telemetry.build_resource(telemetry.INGEST_SERVICE).attributes["service.name"] == (
        "debrief-ingest"
    )
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=hackathon")
    attrs = telemetry.build_resource(telemetry.API_SERVICE).attributes
    assert attrs["deployment.environment"] == "hackathon"
