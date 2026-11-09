"""OpenTelemetry for Debrief (task 11): traces, metrics and logs, exported to Dash0.

`setup()` runs once per process: at API startup (api/main.py) and at the start of an ingest
run (ingest/index.py, ingest/download.py). Config comes only from the standard env vars:

- `OTEL_EXPORTER_OTLP_ENDPOINT`, e.g. https://ingress.<region>.aws.dash0.com (OTLP/HTTP)
- `OTEL_EXPORTER_OTLP_HEADERS`, e.g. Authorization=Bearer%20<token>,Dash0-Dataset=default
- `OTEL_SERVICE_NAME` for the API (default `debrief-api`); ingest runs always report as
  `debrief-ingest`, so `make index` shows up as its own service even with the API's .env
- `OTEL_RESOURCE_ATTRIBUTES` may set `deployment.environment`; `OTEL_SDK_DISABLED=true` turns
  everything off.

Without an endpoint no exporter is installed, so local runs and tests stay offline; spans still
get real trace ids, which the playlist returns. `DEBRIEF_OTEL_CONSOLE=1` prints one line per
span, log and metric point to stderr for local checks.

Logs: every `debrief.*` logger, one `event=<name>` line per product event (`log_event`), and
uvicorn's access log (one line per request, probes dropped) all reach Dash0 with trace ids.

Spans, metric attributes and log lines carry ids, counts, scores and token totals only, never
transcript text, the pain sentence, prompts or keys.
"""

from __future__ import annotations

import logging
import os
import sys
import warnings
from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, ReadableLogRecord
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricReader,
    MetricsData,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import OTELResourceDetector, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

API_SERVICE = "debrief-api"
INGEST_SERVICE = "debrief-ingest"
# Probes hit these every few seconds; tracing them would bury the real requests.
EXCLUDED_URLS = r"/healthz$,/readyz$"
CONSOLE_METRIC_INTERVAL_MS = 10_000
MAX_ID_CHARS = 128
PROBE_PATHS = frozenset({"/healthz", "/readyz"})
CLIP_LABELS = frozenset({"deep_dive", "intro", "pitch"})
ACCESS_LOGGER = "uvicorn.access"

log = logging.getLogger("debrief.telemetry")
events = logging.getLogger("debrief.events")

_state: dict[str, Any] = {}


def enabled() -> bool:
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() != "true"


def _endpoint() -> str:
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()


def _console() -> bool:
    return os.environ.get("DEBRIEF_OTEL_CONSOLE", "").strip() == "1"


def build_resource(service: str) -> Resource:
    """service.name, service.version (APP_VERSION) and deployment.environment.

    For the API, OTEL_SERVICE_NAME wins for the name. OTEL_RESOURCE_ATTRIBUTES wins for the
    environment; inside Kubernetes it defaults to "production", elsewhere to "local".
    """
    if service == API_SERVICE:
        service = os.environ.get("OTEL_SERVICE_NAME", "").strip() or service
    attributes: dict[str, str] = {
        "service.name": service,
        "service.version": app_version(),
        "service.namespace": "debrief",
    }
    from_env = OTELResourceDetector().detect().attributes
    if "deployment.environment" not in from_env:
        environment = deployment_environment()
        attributes["deployment.environment"] = environment
        attributes["deployment.environment.name"] = environment
    return Resource.create(attributes)


def app_version() -> str:
    """The commit's short SHA (APP_VERSION, set by the image build), or "dev"."""
    return os.environ.get("APP_VERSION", "").strip() or "dev"


def deployment_environment() -> str:
    """`deployment.environment` from OTEL_RESOURCE_ATTRIBUTES, else production in
    Kubernetes and local elsewhere. The browser (GET /api/config) reports the same value."""
    from_env = OTELResourceDetector().detect().attributes.get("deployment.environment")
    if from_env:
        return str(from_env)
    return "production" if os.environ.get("KUBERNETES_SERVICE_HOST") else "local"


# --- Console output for local checks (DEBRIEF_OTEL_CONSOLE=1) ---------------------------


def _span_line(span: ReadableSpan) -> str:
    ctx = span.context
    parent = format(span.parent.span_id, "016x") if span.parent else "-" * 16
    ms = ((span.end_time or 0) - (span.start_time or 0)) / 1e6
    return (
        f"[span] trace={format(ctx.trace_id, '032x')} span={format(ctx.span_id, '016x')} "
        f"parent={parent} {ms:8.1f}ms {span.name}\n"
    )


def _log_line(record: ReadableLogRecord) -> str:
    r = record.log_record
    trace_id = format(r.trace_id, "032x") if r.trace_id else "-"
    attrs = {k: v for k, v in (r.attributes or {}).items() if not k.startswith("code.")}
    return f"[log] trace={trace_id} {r.severity_text} {r.body} {attrs}\n"


def _metric_lines(data: MetricsData) -> str:
    lines = []
    for resource_metrics in data.resource_metrics:
        for scope in resource_metrics.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    value = getattr(point, "value", None)
                    if value is None:  # histogram
                        value = f"count={point.count} sum={round(point.sum, 1)}"
                    lines.append(f"[metric] {metric.name} {dict(point.attributes or {})} {value}\n")
    return "".join(lines)


# --- Setup -----------------------------------------------------------------------------


def otel_log_handler(provider: LoggerProvider) -> logging.Handler:
    """Python `logging` -> OTel logs bridge. Records keep the current trace and span ids."""
    with warnings.catch_warnings():
        # The SDK handler still works; its successor lives in an extra package we don't need.
        warnings.simplefilter("ignore", DeprecationWarning)
        return LoggingHandler(level=logging.INFO, logger_provider=provider)


class ProbeFilter(logging.Filter):
    """Drops uvicorn access lines for /healthz and /readyz: probes would bury real requests."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn logs '%s - "%s %s HTTP/%s" %d' with (client, method, path, version, status)
        if isinstance(args, tuple) and len(args) >= 3:
            return str(args[2]).split("?", 1)[0] not in PROBE_PATHS
        return True


def attach_access_log(handler: logging.Handler) -> None:
    """Send uvicorn's access lines (one per request) to `handler`, probes excluded.

    uvicorn's `uvicorn.access` logger doesn't propagate to the root, so the OTel handler goes
    on it directly. The line is logged while the response starts, inside the request's server
    span, so it carries the request's trace id. uvicorn configures its loggers before it
    imports the app, so this handler survives its dictConfig.
    """
    access = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, ProbeFilter) for f in access.filters):
        access.addFilter(ProbeFilter())
    if access.level == logging.NOTSET:
        access.setLevel(logging.INFO)
    access.addHandler(handler)


def setup(service: str = API_SERVICE) -> bool:
    """Install the tracer, meter and logger providers once. Returns True if it did."""
    if _state or not enabled():
        return False
    # The Elastic client adds its own span per request; never let it record the query body,
    # which holds the pain sentence.
    os.environ["OTEL_PYTHON_INSTRUMENTATION_ELASTICSEARCH_CAPTURE_SEARCH_QUERY"] = "omit"
    endpoint, console = _endpoint(), _console()
    resource = build_resource(service)

    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    logger_provider = LoggerProvider(resource=resource)
    if endpoint:
        # Imported here so a run without an endpoint never loads the HTTP exporters.
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # The exporters read the endpoint and headers from the env and add /v1/<signal>.
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    if console:
        tracer_provider.add_span_processor(
            SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr, formatter=_span_line))
        )
        readers.append(
            PeriodicExportingMetricReader(
                ConsoleMetricExporter(out=sys.stderr, formatter=_metric_lines),
                export_interval_millis=CONSOLE_METRIC_INTERVAL_MS,
            )
        )
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(ConsoleLogRecordExporter(out=sys.stderr, formatter=_log_line))
        )

    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))
    debrief_logger = logging.getLogger("debrief")
    debrief_logger.setLevel(logging.INFO)
    if endpoint or console:
        from opentelemetry._logs import set_logger_provider

        set_logger_provider(logger_provider)
        debrief_logger.addHandler(otel_log_handler(logger_provider))
        attach_access_log(otel_log_handler(logger_provider))
    _state.update(tracer=tracer_provider, logger=logger_provider, exporting=bool(endpoint))
    return True


def instrument_app(app: Any, meter_provider: Any = None) -> None:
    """Trace every FastAPI request except the probes, and add `X-Trace-Id` to /api replies.

    The instrumentation also records `http.server.duration` (ms, by route, method and status)
    and `http.server.active_requests` through the global meter provider (tests pass their own).
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    app.add_middleware(TraceIdHeaderMiddleware)
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=EXCLUDED_URLS,
        exclude_spans=["receive", "send"],
        meter_provider=meter_provider,
    )


def flush() -> None:
    """Export whatever is buffered; ingest calls this before the process exits."""
    for key in ("tracer", "logger"):
        provider = _state.get(key)
        if provider is not None:
            provider.force_flush()
    meter_provider = metrics.get_meter_provider()
    if isinstance(meter_provider, MeterProvider):
        meter_provider.force_flush()


def current_trace_id() -> str | None:
    """The current trace id as 32 hex characters, or None when tracing is off."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class TraceIdHeaderMiddleware:
    """Adds `X-Trace-Id: <32 hex>` to every /api response, so a reply can be found in Dash0.

    Runs inside the OpenTelemetry middleware, so the current span is the request's server span.
    """

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        trace_id = current_trace_id() if scope["type"] == "http" else None
        if trace_id is None or not str(scope.get("path", "")).startswith("/api"):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", trace_id.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)


# --- Metrics ----------------------------------------------------------------------------


class Instruments:
    """Every Debrief metric. Attributes stay low-cardinality: known tool ids, enums."""

    def __init__(self, meter: Meter) -> None:
        self.playlist_duration = meter.create_histogram(
            "debrief.playlist.duration", unit="ms", description="Time to build a playlist"
        )
        self.playlist_clips = meter.create_histogram(
            "debrief.playlist.clips", unit="{clip}", description="Clips in a playlist"
        )
        self.playlist_empty = meter.create_counter(
            "debrief.playlist.empty",
            unit="{tool}",
            description="Requested tools in playlists that came back empty (coverage gaps)",
        )
        self.citations_dropped = meter.create_counter(
            "debrief.memo.citations_dropped",
            unit="{line}",
            description="Memo lines dropped because they cite no requested clip",
        )
        self.memo_lines = meter.create_counter(
            "debrief.memo.lines",
            unit="{line}",
            description="Memo lines the citation check looked at, kept or dropped (SLO total)",
        )
        self.llm_tokens = meter.create_counter(
            "debrief.llm.tokens", unit="{token}", description="Model tokens used"
        )
        self.tool_requests = meter.create_counter(
            "debrief.playlist.tool_requests",
            unit="{request}",
            description="Playlist requests per requested tool (catalog id, else other)",
        )
        self.clip_labels = meter.create_counter(
            "debrief.playlist.clip_labels",
            unit="{clip}",
            description="Clips served in playlists, by their talk's label",
        )
        self.rerank_batches = meter.create_counter(
            "debrief.rerank.batches",
            unit="{batch}",
            description="Model rerank batches by outcome (ok, fallback, timeout)",
        )
        self.memo_duration = meter.create_histogram(
            "debrief.memo.duration", unit="ms", description="Time to write a live memo"
        )
        self.cache_lookups = meter.create_counter(
            "debrief.cache.lookups",
            unit="{lookup}",
            description="Demo cache lookups by route and result (only with DEBRIEF_CACHE=on)",
        )
        self.playlists_shared = meter.create_counter(
            "debrief.playlists.shared", unit="{playlist}", description="Playlists saved to share"
        )


# Created from the global (proxy) meter, so they report through whatever provider setup()
# installs. Tests swap this for instruments on an in-memory reader.
instruments = Instruments(metrics.get_meter("debrief"))


def record_playlist(
    duration_ms: float, clips: int, tools: Sequence[str], filter_: str, outcome: str
) -> None:
    """One playlist request. `tools` must already be known tool ids (or "other").

    `outcome` is "ok", "empty" or "error". An empty playlist counts once per requested tool.
    """
    attrs = {"filter": filter_, "outcome": outcome}
    instruments.playlist_duration.record(duration_ms, attrs)
    if outcome == "error":
        return
    instruments.playlist_clips.record(clips, {"filter": filter_})
    if outcome == "empty":
        for tool in tools:
            instruments.playlist_empty.add(1, {"tool": tool, "filter": filter_})


DROP_REASONS = frozenset({"uncited", "unknown_clip"})


def record_memo_line_dropped(reason: str, cited_clip_id: str | None = None) -> None:
    """Task 9 calls this for each memo line the citation check drops.

    `reason` is "uncited" (no clip id) or "unknown_clip" (cites an id not in the request).
    Counts `debrief.memo.citations_dropped` and logs the ids, never the line's text.
    """
    reason = reason if reason in DROP_REASONS else "other"
    instruments.citations_dropped.add(1, {"reason": reason})
    log.info(
        "memo line dropped",
        extra={
            "debrief.reason": reason,
            "debrief.cited_clip_id": (cited_clip_id or "")[:MAX_ID_CHARS],
        },
    )


def record_memo_lines_checked(count: int) -> None:
    """Task 9 calls this once per memo with every line the citation check looked at.

    With `debrief.memo.citations_dropped` it gives the "memo lines carry a valid citation" SLO:
    good = lines - dropped.
    """
    if count > 0:
        instruments.memo_lines.add(count)


def record_llm_tokens(purpose: str, provider: str, input_tokens: int, output_tokens: int) -> None:
    """Tasks 8/9 call this after each model call: purpose "rerank" | "memo" | "label",
    provider "anthropic" | "openai". Also puts the totals on the current span.
    """
    for token_type, count in (("input", input_tokens), ("output", output_tokens)):
        if count:
            instruments.llm_tokens.add(
                count, {"purpose": purpose, "provider": provider, "token_type": token_type}
            )
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attributes(
            {
                "gen_ai.provider.name": provider,
                "gen_ai.usage.input_tokens": input_tokens,
                "gen_ai.usage.output_tokens": output_tokens,
            }
        )


def record_playlist_served(tools: Sequence[str], labels: Sequence[str | None]) -> None:
    """One served playlist (live or cached): each distinct requested tool once (known ids or
    "other", see routes_search.metric_tools) and each clip once by its talk label."""
    for tool in dict.fromkeys(tools):
        instruments.tool_requests.add(1, {"tool": tool})
    for label in labels:
        instruments.clip_labels.add(1, {"label": label if label in CLIP_LABELS else "unlabelled"})


def rerank_outcome(outcome: str) -> str:
    """A rerank batch's outcome as a fixed enum: "ok", "timeout" or "fallback" (any error)."""
    return outcome if outcome in ("ok", "timeout") else "fallback"


def record_rerank_batch(outcome: str) -> None:
    instruments.rerank_batches.add(1, {"outcome": rerank_outcome(outcome)})


def record_memo(duration_ms: float, outcome: str) -> None:
    """One live memo; `outcome` is "ok" or "error"."""
    instruments.memo_duration.record(duration_ms, {"outcome": outcome})


def record_cache_lookup(route: str, hit: bool) -> None:
    """One demo cache lookup (only called with DEBRIEF_CACHE=on); `route` is a cache route."""
    instruments.cache_lookups.add(1, {"route": route, "result": "hit" if hit else "miss"})


def record_playlist_shared() -> None:
    instruments.playlists_shared.add(1)


# --- Event logs -------------------------------------------------------------------------


def _field_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ",".join(map(str, value)) or "-"
    text = str(value)
    return f'"{text}"' if " " in text else text


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """One structured log line per product event, on the current trace.

    The body reads `event=<name> key=value ...`; the same fields go on the record as
    `debrief.<key>` attributes next to `event.name`, so Dash0 can filter and group on them.
    Fields must be ids, counts, scores, durations or fixed enums: never transcript text, the
    pain sentence, prompts or keys. None values are left out.
    """
    attributes: dict[str, Any] = {"event.name": event}
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, float):
            value = round(value, 1)
        elif isinstance(value, list | tuple):
            value = [str(v)[:MAX_ID_CHARS] for v in value]
        elif isinstance(value, str):
            value = value[:MAX_ID_CHARS]
        attributes[f"debrief.{key}"] = value
        parts.append(f"{key}={_field_text(value)}")
    events.log(level, " ".join(parts), extra=attributes)
