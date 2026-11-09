"""Shared test setup: no test may reach a real model provider."""

from collections.abc import Iterator

import pytest

from api import llm


@pytest.fixture(autouse=True)
def offline_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the default model client at a provider that always fails, without network.

    Tests that need a model override `routes_search.model_client` with a fake.
    """
    monkeypatch.setenv("LLM_PROVIDER", "offline-test")
    llm.get_llm.cache_clear()
    yield
    llm.get_llm.cache_clear()
