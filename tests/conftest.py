"""Shared test setup: telemetry never exports from a test run, even if the shell has OTEL_* set."""

import os

for _name in (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_SDK_DISABLED",
    "DEBRIEF_OTEL_CONSOLE",
):
    os.environ.pop(_name, None)
