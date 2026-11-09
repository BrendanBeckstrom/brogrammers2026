"""The only module that talks to a language model provider (tasks 8, 9 and 12).

`complete(prompt, purpose=..., max_tokens=...)` sends one prompt and returns the text
plus token counts where the provider reports them. `LLM_PROVIDER` picks the backend:

- `elastic` (default): the Elastic Inference Service through the Elasticsearch client
  (`ELASTIC_URL`, `ELASTIC_API_KEY`); `LLM_RERANK_MODEL` / `LLM_MEMO_MODEL` are
  inference ids.
- `anthropic`: the Claude API with `LLM_API_KEY`.
- `openai`: the OpenAI API with `LLM_API_KEY`.

Callers take an `LLMClient`, so tests swap in a fake and never touch the network. Every
call runs in one span carrying provider, model, purpose, token counts and duration;
prompts and completions never go on spans or in logs.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal, Protocol

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

Purpose = Literal["rerank", "memo", "label"]

DEFAULT_PROVIDER = "elastic"
DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "elastic": {
        "rerank": ".anthropic-claude-5-sonnet-completion",
        "memo": ".anthropic-claude-5-sonnet-completion",
    },
    "anthropic": {"rerank": "claude-haiku-5-5", "memo": "claude-sonnet-5-5"},
    "openai": {"rerank": "gpt-5-mini", "memo": "gpt-5"},
}
# Effort per purpose for providers that take one: short JSON jobs need little thinking.
EFFORT: dict[str, str] = {"rerank": "low", "label": "low", "memo": "medium"}
SYSTEM_PROMPT = (
    "You help an engineering team decide which conference talk clips to watch. "
    "Use only the transcript text you are given; never invent talks, speakers or advice."
)
JSON_INSTRUCTION = " Reply with JSON only: no prose before or after it and no code fences."

log = logging.getLogger("debrief.llm")
tracer = trace.get_tracer("debrief")


class LLMError(RuntimeError):
    """The model call failed. The message names the failure, never the prompt or a key."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: float = 0.0


class LLMClient(Protocol):
    """What callers need; `LLM` implements it and tests pass a fake."""

    def complete(
        self,
        prompt: str,
        *,
        purpose: Purpose,
        max_tokens: int,
        json: bool = False,
        timeout: float | None = None,
    ) -> LLMResult: ...


class Backend(Protocol):
    """One provider. Returns (text, input_tokens, output_tokens); raises on failure."""

    name: str

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        purpose: Purpose,
        max_tokens: int,
        json: bool,
        timeout: float,
    ) -> tuple[str, int | None, int | None]: ...


# --- Backends -------------------------------------------------------------------------


class ElasticBackend:
    """Elastic Inference Service `completion` endpoints; the model id is an inference id."""

    name = "elastic"

    def __init__(self, url: str, api_key: str) -> None:
        self._url = url
        self._api_key = api_key
        self._es: Any = None

    def _client(self) -> Any:
        if not self._url or not self._api_key:
            raise LLMError("ELASTIC_URL and ELASTIC_API_KEY must be set")
        if self._es is None:
            from elasticsearch import Elasticsearch

            self._es = Elasticsearch(self._url, api_key=self._api_key, max_retries=0)
        return self._es

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        purpose: Purpose,
        max_tokens: int,
        json: bool,
        timeout: float,
    ) -> tuple[str, int | None, int | None]:
        es = self._client().options(request_timeout=timeout)
        text = SYSTEM_PROMPT + (JSON_INSTRUCTION if json else "") + "\n\n" + prompt
        response = es.inference.completion(
            inference_id=model, input=text, timeout=f"{max(1, int(timeout))}s"
        )
        results = (response.body or {}).get("completion") or []
        if not results:
            raise LLMError("elastic returned no completion")
        return str(results[0].get("result") or ""), None, None  # EIS reports no usage


class AnthropicBackend:
    """The Claude API through the official SDK (pinned in pyproject.toml)."""

    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._sdk: Any = None

    def _client(self) -> Any:
        if not self._api_key:
            raise LLMError("LLM_API_KEY must be set for LLM_PROVIDER=anthropic")
        if self._sdk is None:
            import anthropic

            self._sdk = anthropic.Anthropic(api_key=self._api_key, max_retries=0)
        return self._sdk

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        purpose: Purpose,
        max_tokens: int,
        json: bool,
        timeout: float,
    ) -> tuple[str, int | None, int | None]:
        response = (
            self._client()
            .with_options(timeout=timeout)
            .beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT + (JSON_INSTRUCTION if json else ""),
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": EFFORT.get(purpose, "low")},
                # On a policy decline, retry on the server's default fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        )
        if response.stop_reason == "refusal":
            raise LLMError("anthropic declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("anthropic hit max_tokens")
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return text, usage.input_tokens, usage.output_tokens


class OpenAIBackend:
    """The OpenAI chat completions API (lightly tested; pinned in pyproject.toml)."""

    name = "openai"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._sdk: Any = None

    def _client(self) -> Any:
        if not self._api_key:
            raise LLMError("LLM_API_KEY must be set for LLM_PROVIDER=openai")
        if self._sdk is None:
            import openai

            self._sdk = openai.OpenAI(api_key=self._api_key, max_retries=0)
        return self._sdk

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        purpose: Purpose,
        max_tokens: int,
        json: bool,
        timeout: float,
    ) -> tuple[str, int | None, int | None]:
        extra: dict[str, Any] = {"response_format": {"type": "json_object"}} if json else {}
        response = self._client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + (JSON_INSTRUCTION if json else "")},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=max_tokens,
            timeout=timeout,
            **extra,
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("openai hit max_completion_tokens")
        usage = response.usage
        return (
            choice.message.content or "",
            usage.prompt_tokens if usage else None,
            usage.completion_tokens if usage else None,
        )


class UnconfiguredBackend:
    """Stands in for an unknown LLM_PROVIDER: every call fails, so callers fall back."""

    def __init__(self, name: str) -> None:
        self.name = name

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        purpose: Purpose,
        max_tokens: int,
        json: bool,
        timeout: float,
    ) -> tuple[str, int | None, int | None]:
        raise LLMError(f"unknown LLM_PROVIDER {self.name!r}")


# --- The client callers use -----------------------------------------------------------


class LLM:
    """`LLMClient` over one backend: picks the model per purpose and traces every call."""

    def __init__(
        self,
        backend: Backend,
        models: Mapping[str, str],
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.backend = backend
        self.models = dict(models)
        self.timeout = timeout

    @property
    def provider(self) -> str:
        return self.backend.name

    def model_for(self, purpose: Purpose) -> str:
        return self.models.get(purpose) or self.models.get("rerank") or ""

    def complete(
        self,
        prompt: str,
        *,
        purpose: Purpose,
        max_tokens: int,
        json: bool = False,
        timeout: float | None = None,
    ) -> LLMResult:
        model = self.model_for(purpose)
        with tracer.start_as_current_span("llm.complete") as span:
            span.set_attributes(
                {
                    "gen_ai.system": self.provider,
                    "gen_ai.request.model": model,
                    "gen_ai.request.max_tokens": max_tokens,
                    "debrief.llm.purpose": purpose,
                    "debrief.llm.json": json,
                    "debrief.llm.prompt_chars": len(prompt),
                }
            )
            started = time.perf_counter()
            try:
                text, input_tokens, output_tokens = self.backend.generate(
                    prompt,
                    model=model,
                    purpose=purpose,
                    max_tokens=max_tokens,
                    json=json,
                    timeout=timeout or self.timeout,
                )
                if not text.strip():
                    raise LLMError(f"{self.provider} returned an empty completion")
            except Exception as e:
                duration = round((time.perf_counter() - started) * 1000, 1)
                reason = type(e).__name__
                span.set_attributes({"debrief.duration_ms": duration, "error.type": reason})
                span.set_status(Status(StatusCode.ERROR, reason))
                log.warning(
                    "llm call failed provider=%s model=%s purpose=%s error=%s duration_ms=%s",
                    self.provider,
                    model,
                    purpose,
                    reason,
                    duration,
                )
                if isinstance(e, LLMError):
                    raise
                raise LLMError(f"{self.provider} call failed ({reason})") from e
            duration = round((time.perf_counter() - started) * 1000, 1)
            span.set_attribute("debrief.duration_ms", duration)
            span.set_attribute("debrief.llm.completion_chars", len(text))
            if input_tokens is not None:
                span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            if output_tokens is not None:
                span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            return LLMResult(text, self.provider, model, input_tokens, output_tokens, duration)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def build_llm(env: Mapping[str, str] | None = None) -> LLM:
    """An `LLM` for the provider and models named in the environment."""
    get = (lambda k: (env.get(k) or "").strip()) if env is not None else _env
    provider = get("LLM_PROVIDER").lower() or DEFAULT_PROVIDER
    backend: Backend
    if provider == "elastic":
        backend = ElasticBackend(get("ELASTIC_URL"), get("ELASTIC_API_KEY"))
    elif provider == "anthropic":
        backend = AnthropicBackend(get("LLM_API_KEY"))
    elif provider == "openai":
        backend = OpenAIBackend(get("LLM_API_KEY"))
    else:
        backend = UnconfiguredBackend(provider)
    defaults = DEFAULT_MODELS.get(provider, {})
    rerank_model = get("LLM_RERANK_MODEL") or defaults.get("rerank", "")
    models = {
        "rerank": rerank_model,
        "label": rerank_model,
        "memo": get("LLM_MEMO_MODEL") or defaults.get("memo", ""),
    }
    return LLM(backend, models)


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    """The process-wide model client, built from the environment on first use."""
    return build_llm()


def complete(
    prompt: str,
    *,
    purpose: Purpose,
    max_tokens: int,
    json: bool = False,
    timeout: float | None = None,
) -> LLMResult:
    """Send one prompt to the configured provider. Raises LLMError on any failure."""
    return get_llm().complete(
        prompt, purpose=purpose, max_tokens=max_tokens, json=json, timeout=timeout
    )
