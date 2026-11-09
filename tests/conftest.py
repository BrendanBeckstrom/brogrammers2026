"""Shared test setup.

- Telemetry never exports from a test run, even if the shell has OTEL_* set.
- No test may reach a real model provider, or the real Elastic (the query log writes in the
  background on every playlist, so ELASTIC_* is cleared; tests that need it set it).
"""

import os
from collections.abc import Iterator

import pytest

from api import llm

for _name in (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_SDK_DISABLED",
    "DEBRIEF_OTEL_CONSOLE",
    "ELASTIC_URL",
    "ELASTIC_API_KEY",
):
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def offline_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the default model client at a provider that always fails, without network.

    Tests that need a model override `routes_search.model_client` with a fake.
    """
    monkeypatch.setenv("LLM_PROVIDER", "offline-test")
    llm.get_llm.cache_clear()
    yield
    llm.get_llm.cache_clear()
