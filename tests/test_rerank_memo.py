"""Tasks 8 and 9: model rerank + why lines, and the cited memo, against fakes only."""

import json
import re
import statistics
import threading
import time
from collections import Counter
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from api import llm, memo, rerank, routes_memo, routes_search, search, telemetry
from api.clips import count_mentions
from api.llm import LLMError, LLMResult
from api.main import app
from tests import golden
from tests.test_search import CHUNKS, TALK_IDS, TALKS, FakeSearchClient

GITOPS = {"tools": ["argo-cd", "prometheus"], "pain": "our Argo CD syncs are slow"}
NAMES = {"argo-cd": "Argo CD", "prometheus": "Prometheus", "istio": "Istio", "envoy": "Envoy"}

Reply = str | Exception | Callable[[str], str]


class FakeLLM:
    """An `LLMClient` that answers from a canned reply (or a function of the prompt)."""

    def __init__(self, reply: Reply) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        prompt: str,
        *,
        purpose: str,
        max_tokens: int,
        json: bool = False,
        timeout: float | None = None,
    ) -> LLMResult:
        self.calls.append((purpose, prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply(prompt) if callable(self.reply) else self.reply
        return LLMResult(text, "fake", "fake-model", 120, 60, 1.0)


def prompt_clips(prompt: str) -> list[dict[str, Any]]:
    block = prompt.split("<clips>\n", 1)[1].split("\n</clips>", 1)[0]
    return [json.loads(line) for line in block.splitlines()]


def scored_reply(
    score: Callable[[int, dict[str, Any]], float] = lambda i, c: round(1 - i / 40, 3),
    why: Callable[[dict[str, Any]], str] | None = None,
    extra: list[dict[str, Any]] | None = None,
) -> Callable[[str], str]:
    """A rerank reply scoring every clip in the prompt; the default why names its tool."""

    def default_why(clip: dict[str, Any]) -> str:
        if not clip["tools"]:
            return "Close to what hurts"
        return f"You run {clip['tools'][0]}; this shows how they tuned it"

    def reply(prompt: str) -> str:
        entries = [
            {"clip_id": c["clip_id"], "score": score(i, c), "why": (why or default_why)(c)}
            for i, c in enumerate(prompt_clips(prompt))
        ]
        return json.dumps(entries + (extra or []))

    return reply


@pytest.fixture
def fake() -> FakeSearchClient:
    return FakeSearchClient(CHUNKS, TALKS)


@pytest.fixture
def use_model(fake: FakeSearchClient):
    """Returns a function that installs a FakeLLM and hands back a TestClient."""

    def install(model: FakeLLM | None) -> TestClient:
        app.dependency_overrides[routes_search.search_client] = lambda: fake
        if model is not None:
            app.dependency_overrides[routes_search.model_client] = lambda: model
        return TestClient(app)

    yield install
    app.dependency_overrides.clear()


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("debrief")
    for module in (search, routes_search, routes_memo, llm, rerank):
        monkeypatch.setattr(module, "tracer", tracer)
    return exporter


def names_a_matched_tool(clip: dict[str, Any]) -> bool:
    catalog = search.tool_catalog()
    return any(
        count_mentions(clip["why"], [catalog[t].name, *catalog[t].terms])
        for t in clip["matched_tools"]
    )


def playlist(api: TestClient, **body: Any) -> dict[str, Any]:
    response = api.post("/api/playlist", json={"rerank": True, **GITOPS, **body})
    assert response.status_code == 200, response.text
    return response.json()


def elastic_order(fake: FakeSearchClient, **body: Any) -> list[str]:
    """Clip ids with the Elastic rerank but no model step: what a fallback must return."""
    req = search.PlaylistRequest(**{**GITOPS, **body, "rerank": True})
    return [c["clip_id"] for c in search.build_playlist(fake, req, None)["clips"]]


# --- Task 8 check: why lines name a matched tool; model failure keeps Elastic's order ---


def test_every_why_line_names_a_matched_tool(use_model) -> None:
    model = FakeLLM(scored_reply())
    data = playlist(use_model(model))
    assert data["debug"]["rerank"] == {"elastic": "ok", "model": "ok"}
    clips = data["clips"]
    assert any(c["why"].startswith("You run ") for c in clips)  # the model's lines are used
    for clip in clips:
        assert len(clip["why"].split()) <= 20
        if clip["matched_tools"]:
            assert names_a_matched_tool(clip), clip
    assert "rerank" in data["debug"]["timings_ms"]


@pytest.mark.parametrize(
    "error", [LLMError("elastic call failed (AuthorizationException)"), TimeoutError("slow")]
)
def test_model_failure_returns_elastic_order_not_an_error(
    use_model, fake: FakeSearchClient, error: Exception
) -> None:
    if isinstance(error, TimeoutError):  # a real LLM wraps any backend error in LLMError
        model: Any = llm.LLM(RaisingBackend(error), {"rerank": "m", "memo": "m"})
    else:
        model = FakeLLM(error)
    data = playlist(use_model(model))
    assert [c["clip_id"] for c in data["clips"]] == elastic_order(fake)
    assert all(c["why"].startswith("Mentions ") for c in data["clips"] if c["matched_tools"])
    assert data["debug"]["rerank"]["model"] == "fallback: LLMError"


def test_invalid_json_falls_back_to_elastic_order(use_model, fake: FakeSearchClient) -> None:
    data = playlist(use_model(FakeLLM("Sure! Here are the clips you asked for.")))
    assert [c["clip_id"] for c in data["clips"]] == elastic_order(fake)
    assert data["debug"]["rerank"]["model"] == "fallback: RerankError"


def test_truncated_json_falls_back(use_model, fake: FakeSearchClient) -> None:
    data = playlist(use_model(FakeLLM('[{"clip_id": "x", "score": 0.9, "why": "Argo CD')))
    assert [c["clip_id"] for c in data["clips"]] == elastic_order(fake)


def test_reply_naming_only_unknown_clips_falls_back(use_model, fake: FakeSearchClient) -> None:
    reply = json.dumps([{"clip_id": "talkz0000-0", "score": 1, "why": "Argo CD rocks"}])
    data = playlist(use_model(FakeLLM(reply)))
    assert [c["clip_id"] for c in data["clips"]] == elastic_order(fake)


def test_why_that_names_no_tool_gets_the_template(use_model) -> None:
    long = "Argo CD " + " ".join(["word"] * 25)
    whys = iter(["A great talk about scaling things", long] * 50)
    data = playlist(use_model(FakeLLM(scored_reply(why=lambda c: next(whys)))))
    assert data["debug"]["rerank"]["model"] == "ok"
    for clip in data["clips"]:
        assert "great talk" not in clip["why"] and "word word" not in clip["why"]
        if clip["matched_tools"]:
            assert clip["why"].startswith("Mentions ") and names_a_matched_tool(clip)


def test_why_matches_tool_aliases_case_insensitively() -> None:
    terms = {"argo-cd": ("argo cd", "argocd")}
    names = {"argo-cd": "Argo CD"}
    assert rerank.valid_why("Tuning ARGOCD sync waves", ["argo-cd"], names, terms)
    assert rerank.valid_why("'Argo  CD at scale'", ["argo-cd"], names, terms) == "Argo CD at scale"
    assert rerank.valid_why("Argo Workflows at scale", ["argo-cd"], names, terms) is None
    assert rerank.valid_why("Argo CD", [], names, terms) is None


def test_unknown_ids_dropped_and_order_follows_model_score(use_model) -> None:
    extra = [{"clip_id": "talkz0000-60", "score": 1.0, "why": "Argo CD invented clip"}]
    model = FakeLLM(scored_reply(score=lambda i, c: round(0.4 + (i % 7) / 10, 2), extra=extra))
    data = playlist(use_model(model))
    ids = [c["clip_id"] for c in data["clips"]]
    assert "talkz0000-60" not in ids
    scores = [c["score"] for c in data["clips"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)


def test_small_batches_with_at_most_18_clips_and_short_excerpts(use_model) -> None:
    model = FakeLLM(scored_reply())
    playlist(use_model(model))
    assert search.clipmod.MAX_CLIPS <= rerank.TOP_N <= 18
    assert 1 < len(model.calls) <= -(-rerank.TOP_N // rerank.BATCH_SIZE)
    sent = [prompt_clips(prompt) for purpose, prompt in model.calls if purpose == "rerank"]
    assert len(sent) == len(model.calls)
    assert all(0 < len(batch) <= rerank.BATCH_SIZE for batch in sent)
    ids = [c["clip_id"] for batch in sent for c in batch]
    assert len(ids) == len(set(ids)) <= rerank.TOP_N  # each clip in exactly one batch
    assert all(len(c["excerpt"]) <= 300 for batch in sent for c in batch)
    for _, prompt in model.calls:
        assert "our Argo CD syncs are slow" in prompt and "Argo CD, Prometheus" in prompt


def test_low_scores_are_dropped_but_never_below_the_golden_minimum(
    use_model, fake: FakeSearchClient
) -> None:
    # Everything scores under the threshold except two clips: the playlist still meets
    # the minimum, with the two high scorers first.
    order = elastic_order(fake)
    high = {order[3], order[5]}
    reply = scored_reply(score=lambda i, c: 0.9 if c["clip_id"] in high else 0.1)
    data = playlist(use_model(FakeLLM(reply)))
    clips = data["clips"]
    assert [c["score"] for c in clips[:2]] == [0.9, 0.9]
    assert len(clips) >= 6 and len({c["talk_id"] for c in clips}) >= 4
    assert data["total_seconds"] >= 900


def test_low_scores_are_dropped_when_coverage_allows() -> None:
    def clip(talk: str, score: float) -> Any:
        hit = search.Hit.from_source(
            {
                "chunk_id": f"{talk}-10",
                "talk_id": talk,
                "start_sec": 10,
                "end_sec": 200,
                "tools": ["x"],
            },  # fmt: skip
            score=score,
        )
        return search.clipmod.Clip((hit,))

    candidates = [clip(f"t{i}", 1.0) for i in range(10)]
    judged = {c.clip_id: rerank.Judgement(0.9 if i < 7 else 0.1, None) for i, c in
              enumerate(candidates)}  # fmt: skip
    chosen = rerank.choose(candidates, judged, budget_sec=1500, tool_ids=["x"])
    assert {c.first.talk_id for c in chosen} == {f"t{i}" for i in range(7)}


def test_golden_checks_pass_with_rerank_on_fake(use_model) -> None:
    api = use_model(FakeLLM(scored_reply(score=lambda i, c: 0.95 - (i % 9) / 10)))
    for stack in golden.load_stacks():
        if stack.get("skip"):
            continue
        if stack.get("attended_talk_ids"):
            stack = {**stack, "attended_talk_ids": [TALK_IDS[0]]}
        response = api.post("/api/playlist", json=golden.playlist_request(stack, rerank=True))
        assert response.status_code == 200
        failed = [c for c in golden.check_playlist(stack, response.json()) if not c.ok]
        assert not failed, (stack["name"], failed)


# --- Elastic rerank fallback --------------------------------------------------------


def test_elastic_rerank_error_falls_back_to_rrf(
    use_model, fake: FakeSearchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = fake.search

    def search_(index: str, body: dict[str, Any]) -> dict[str, Any]:
        if "text_similarity_reranker" in body.get("retriever", {}):
            raise search.SearchBackendError("Elastic search failed (BadRequestError)")
        return original(index, body)

    monkeypatch.setattr(fake, "search", search_)
    data = playlist(use_model(None))
    assert data["clips"]
    assert data["debug"]["rerank"]["elastic"] == "fallback: error"
    assert "rrf" in data["debug"]["query"]["retriever"]


def test_slow_elastic_rerank_falls_back_to_rrf(
    use_model, fake: FakeSearchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = fake.search

    def search_(index: str, body: dict[str, Any]) -> dict[str, Any]:
        if "text_similarity_reranker" in body.get("retriever", {}):
            time.sleep(0.5)
        return original(index, body)

    monkeypatch.setattr(fake, "search", search_)
    monkeypatch.setattr(search, "ELASTIC_RERANK_TIMEOUT_SEC", 0.05)
    data = playlist(use_model(None))
    assert data["clips"] and data["debug"]["rerank"]["elastic"] == "fallback: timeout"


def test_rerank_off_skips_elastic_rerank_and_model(use_model, fake: FakeSearchClient) -> None:
    model = FakeLLM(scored_reply())
    data = playlist(use_model(model), rerank=False)
    assert not model.calls
    assert data["debug"]["rerank"] == {"elastic": "off", "model": "off"}
    assert not any("text_similarity_reranker" in str(b) for _, b in fake.bodies)


# --- api/llm.py -----------------------------------------------------------------------


class RaisingBackend:
    name = "fake"

    def __init__(self, error: Exception) -> None:
        self.error = error

    def generate(self, prompt: str, **kw: Any) -> tuple[str, int | None, int | None]:
        raise self.error


class EchoBackend:
    name = "fake"

    def generate(self, prompt: str, **kw: Any) -> tuple[str, int | None, int | None]:
        return "[]", 11, 7


def test_llm_span_has_provider_model_tokens_and_no_text(spans: InMemorySpanExporter) -> None:
    client = llm.LLM(EchoBackend(), {"rerank": "small-model", "memo": "big-model"})
    result = client.complete("SECRET PROMPT TEXT", purpose="memo", max_tokens=100, json=True)
    assert (result.text, result.model, result.input_tokens, result.output_tokens) == (
        "[]",
        "big-model",
        11,
        7,
    )
    (span,) = spans.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.system"] == "fake" and attrs["gen_ai.request.model"] == "big-model"
    assert attrs["debrief.llm.purpose"] == "memo"
    assert attrs["gen_ai.usage.input_tokens"] == 11 and attrs["gen_ai.usage.output_tokens"] == 7
    assert "debrief.duration_ms" in attrs
    assert not any("SECRET" in str(v) or "[]" == v for v in attrs.values())


def test_llm_wraps_backend_errors(spans: InMemorySpanExporter) -> None:
    client = llm.LLM(RaisingBackend(ConnectionError("key=abc123")), {"rerank": "m"})
    with pytest.raises(LLMError) as info:
        client.complete("p", purpose="rerank", max_tokens=10)
    assert "abc123" not in str(info.value) and "ConnectionError" in str(info.value)
    (span,) = spans.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR


def test_llm_rejects_empty_completion() -> None:
    class Empty(EchoBackend):
        def generate(self, prompt: str, **kw: Any) -> tuple[str, int | None, int | None]:
            return "  ", None, None

    with pytest.raises(LLMError):
        llm.LLM(Empty(), {"rerank": "m"}).complete("p", purpose="rerank", max_tokens=10)


def test_build_llm_defaults_and_overrides() -> None:
    default = llm.build_llm({})
    assert default.provider == "elastic"
    assert default.models["rerank"] == ".anthropic-claude-5-sonnet-completion"
    claude = llm.build_llm({"LLM_PROVIDER": "anthropic"})
    assert claude.models == {
        "rerank": "claude-haiku-5-5",
        "label": "claude-haiku-5-5",
        "memo": "claude-sonnet-5-5",
    }
    custom = llm.build_llm({"LLM_PROVIDER": "openai", "LLM_MEMO_MODEL": "big"})
    assert custom.provider == "openai" and custom.models["memo"] == "big"


@pytest.mark.parametrize("provider", ["anthropic", "openai", "elastic", "nope"])
def test_unconfigured_providers_fail_without_network(provider: str) -> None:
    client = llm.build_llm({"LLM_PROVIDER": provider})  # no keys, no URL
    with pytest.raises(LLMError):
        client.complete("p", purpose="rerank", max_tokens=10)


# --- Task 9 check: lines citing unknown ids are dropped; takeaways link requested clips -


def clip_ids_for(api: TestClient, n: int = 8) -> list[str]:
    data = api.post("/api/playlist", json={**GITOPS, "rerank": False}).json()
    return [c["clip_id"] for c in data["clips"][:n]]


def memo_reply(ids: list[str], **override: Any) -> str:
    body: dict[str, Any] = {
        "summary": {
            "text": "Argo CD sync tuning, with Prometheus to watch it.",
            "clip_ids": ids[:2],
        },
        "takeaways": [
            {"text": "Shard the application controller.", "clip_ids": [ids[0]]},
            {"text": "Invented advice from nowhere.", "clip_ids": ["talkz0000-0"]},
            {"text": "Watch sync duration in Prometheus [x].", "clip_ids": [ids[1], ids[2]]},
            {"text": "Use sync waves sparingly.", "clip_ids": [ids[3]]},
            {"text": "Uncited opinion.", "clip_ids": []},
        ],
        "action_items": [
            {"text": "Try controller sharding in staging.", "clip_ids": [ids[0]]},
            {"text": "Half-invented step.", "clip_ids": [ids[1], "nope-1"]},
            {"text": "Add a sync-duration alert.", "clip_ids": [ids[2]]},
        ],
        "watch_together": [
            {"clip_id": ids[0], "reason": "The sharding demo."},
            {"clip_id": "talkz0000-0", "reason": "Does not exist."},
            {"clip_id": ids[2], "reason": "Prometheus dashboards."},
            {"clip_id": ids[3], "reason": ""},
        ],
    }
    body.update(override)
    return "```json\n" + json.dumps(body) + "\n```"


def test_memo_text_removes_links_html_urls_and_escapes_brackets() -> None:
    text = (
        "See [the docs](https://evil.example/docs), "
        "![diagram](https://evil.example/image.png), <b>bold</b> and "
        "https://evil.example/raw [clip_id] [unfinished"
    )
    assert memo.clean_text(text) == "See the docs, diagram, bold and \\[unfinished"


def test_memo_drops_lines_citing_unknown_ids_and_links_every_takeaway(use_model) -> None:
    api = use_model(None)
    ids = clip_ids_for(api)
    model = FakeLLM(memo_reply(ids))
    api = use_model(model)
    response = api.post("/api/memo", json={"tools": GITOPS["tools"], "clip_ids": ids})
    assert response.status_code == 200, response.text
    data = response.json()
    markdown = data["markdown"]
    for dropped in ["Invented advice", "Uncited opinion", "Half-invented", "Does not exist"]:
        assert dropped not in markdown
    assert set(data["cited_clip_ids"]) <= set(ids)
    assert data["cited_clip_ids"][0] == ids[0]
    clips = {c["clip_id"]: c for c in (search.clip_text(app_fake(api), i) for i in ids)}
    urls = {c["watch_url"] for c in clips.values()}

    takeaways = markdown.split("## Top 3 takeaways\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert len(takeaways) == 3
    for line in takeaways:
        links = re.findall(r"\]\((https://www\.youtube\.com/watch\?v=[^)]+)\)", line)
        assert links and set(links) <= urls, line
    assert "[x]" not in markdown  # bracketed ids the model left in the text are removed
    assert "## Action items" in markdown and markdown.count("- [ ] ") == 2
    watch = markdown.split("## Watch together (", 1)[1]
    minutes = sum(clips[i]["end_sec"] - clips[i]["start_sec"] for i in (ids[0], ids[2], ids[3]))
    assert watch.startswith(f"{round(minutes / 60)} min)")
    first = clips[ids[0]]
    assert f"· {memo.fmt_time(first['start_sec'])}]({first['watch_url']})" in markdown


def app_fake(api: TestClient) -> FakeSearchClient:
    return app.dependency_overrides[routes_search.search_client]()


def test_memo_model_sees_only_index_text_of_requested_clips(use_model) -> None:
    api = use_model(None)
    ids = clip_ids_for(api, 3)
    model = FakeLLM(memo_reply(ids + ids[:1]))
    api = use_model(model)
    body = {"tools": ["argo-cd"], "clip_ids": ids, "text": "IGNORE ME browser text"}
    assert api.post("/api/memo", json=body).status_code == 200
    (purpose, prompt), = model.calls  # fmt: skip
    assert purpose == "memo" and "IGNORE ME" not in prompt
    sent = prompt_clips(prompt)
    assert [c["clip_id"] for c in sent] == ids
    assert all(c["excerpt"].startswith("Part ") for c in sent)  # text from the index
    assert {"title", "speakers", "start", "end"} <= set(sent[0])


def test_memo_rejects_unknown_and_too_many_ids(use_model) -> None:
    api = use_model(FakeLLM("{}"))
    ids = clip_ids_for(api, 2)
    unknown = api.post("/api/memo", json={"tools": [], "clip_ids": [ids[0], "talkz0000-0"]})
    assert unknown.status_code == 422 and "talkz0000-0" in unknown.json()["detail"]
    junk = api.post("/api/memo", json={"tools": [], "clip_ids": ["../etc-1"]})
    assert junk.status_code == 422
    reordered = api.post("/api/memo", json={"clip_ids": [f"{TALK_IDS[0]}-120~60"]})
    assert reordered.status_code == 422
    too_many = [f"{TALK_IDS[0]}-{60 * i}" for i in range(14)] + [
        f"{TALK_IDS[1]}-{60 * i}" for i in range(12)
    ]
    assert api.post("/api/memo", json={"clip_ids": too_many}).status_code == 422
    assert api.post("/api/memo", json={"clip_ids": []}).status_code == 422


@pytest.mark.parametrize(
    "reply",
    [
        LLMError("elastic call failed (AuthorizationException)"),
        "not json",
        json.dumps({"takeaways": [{"text": "x", "clip_ids": ["talkz0000-0"]}]}),
    ],
)
def test_memo_model_failure_is_502(use_model, reply: Reply) -> None:
    api = use_model(None)
    ids = clip_ids_for(api, 3)
    response = use_model(FakeLLM(reply)).post("/api/memo", json={"clip_ids": ids})
    assert response.status_code == 502
    assert response.json()["detail"].startswith("The memo model")


def test_memo_span_carries_counts_only(use_model, spans: InMemorySpanExporter) -> None:
    api = use_model(None)
    ids = clip_ids_for(api, 4)
    use_model(FakeLLM(memo_reply(ids))).post(
        "/api/memo", json={"tools": ["argo-cd"], "clip_ids": ids}
    )
    memo_span = next(s for s in spans.get_finished_spans() if s.name == "api.memo")
    attrs = dict(memo_span.attributes)
    assert attrs["debrief.clips_requested"] == 4 and attrs["debrief.memo.takeaways"] == 3
    assert attrs["debrief.memo.dropped_lines"] >= 4
    for value in attrs.values():
        assert "Shard" not in str(value) and "Part " not in str(value)


def test_playlist_spans_cover_rerank_and_carry_no_text(
    use_model, spans: InMemorySpanExporter
) -> None:
    model = FakeLLM(scored_reply())
    playlist(use_model(model))
    finished = spans.get_finished_spans()
    names = Counter(s.name for s in finished)
    assert {"search.elastic_rerank", "rerank.model", "search.budget"} <= set(names)
    assert names["rerank.batch"] == len(model.calls) > 1  # one span per batch
    model_span = next(s for s in finished if s.name == "rerank.model")
    assert model_span.attributes["debrief.rerank.input_tokens"] == 120 * len(model.calls)
    assert model_span.attributes["debrief.rerank.batches_failed"] == 0
    for batch_span in (s for s in finished if s.name == "rerank.batch"):
        assert batch_span.parent.span_id == model_span.context.span_id
        attrs = batch_span.attributes
        assert attrs["debrief.rerank.batch.outcome"] == "ok"
        assert 0 < attrs["debrief.rerank.batch.size"] <= rerank.BATCH_SIZE
        assert "debrief.duration_ms" in attrs and "debrief.rerank.batch.index" in attrs
    assert model_span.attributes["debrief.fallback"] is False
    for span in finished:
        for value in span.attributes.values():
            flat = " ".join(map(str, value)) if isinstance(value, tuple) else str(value)
            assert "syncs are slow" not in flat and "You run" not in flat, span.name


# --- Bounded latency: small parallel batches, each with its own deadline ---------------

TEMPLATE_STARTS = ("Mentions ", "Tagged ", "Close in meaning")


class SleepyLLM(FakeLLM):
    """A FakeLLM that sleeps `delay(prompt)` seconds before answering."""

    def __init__(self, reply: Reply, delay: Callable[[str], float]) -> None:
        super().__init__(reply)
        self.delay = delay

    def complete(self, prompt: str, **kw: Any) -> LLMResult:
        time.sleep(self.delay(prompt))
        return super().complete(prompt, **kw)


class SlowFirstLLM(FakeLLM):
    """Whichever call arrives first sleeps `delay` seconds; the others answer at once."""

    def __init__(self, reply: Reply, delay: float) -> None:
        super().__init__(reply)
        self.delay = delay
        self.lock = threading.Lock()
        self.slow_prompt: str | None = None

    def complete(self, prompt: str, **kw: Any) -> LLMResult:
        with self.lock:
            slow = self.slow_prompt is None
            if slow:
                self.slow_prompt = prompt
        if slow:
            time.sleep(self.delay)
        return super().complete(prompt, **kw)


def synthetic(n: int = 18) -> tuple[list[dict[str, Any]], list[Any]]:
    """`n` one-chunk clips from different talks, and their records, all about Argo CD."""
    clips = []
    for i in range(n):
        source = {"chunk_id": f"talk{i:02d}-10", "talk_id": f"talk{i:02d}", "start_sec": 10,
                  "end_sec": 130, "tools": ["argo-cd"]}  # fmt: skip
        clips.append(search.clipmod.Clip((search.Hit.from_source(source, score=1.0 - i / 100),)))
    records = [
        {"clip_id": c.clip_id, "matched_tools": ["argo-cd"], "quote": "Argo CD syncs."}
        for c in clips
    ]
    return records, clips


def judge(model: Any) -> tuple[rerank.Verdict, float]:
    records, clips = synthetic()
    started = time.perf_counter()
    verdict = rerank.judge(model, records, clips, ["argo-cd"], NAMES, {}, "slow syncs")
    return verdict, time.perf_counter() - started


def test_batches_are_round_robin_and_small() -> None:
    assert rerank.batch_indexes(18) == [
        [0, 3, 6, 9, 12, 15],
        [1, 4, 7, 10, 13, 16],
        [2, 5, 8, 11, 14, 17],
    ]
    assert rerank.batch_indexes(7) == [[0, 2, 4, 6], [1, 3, 5]]
    assert rerank.batch_indexes(2) == [[0, 1]]
    assert rerank.max_tokens_for(rerank.BATCH_SIZE) <= 1000


def test_batches_run_concurrently() -> None:
    delay = 0.3
    verdict, elapsed = judge(SleepyLLM(scored_reply(), lambda prompt: delay))
    assert [b.outcome for b in verdict.batches] == ["ok", "ok", "ok"]
    assert elapsed < 2 * delay < 3 * delay  # parallel: well under the sum of the delays
    assert len(verdict.judged) == 18 and all(j.why for j in verdict.judged.values())
    assert verdict.stats["input_tokens"] == 3 * 120 and verdict.stats["batches_failed"] == 0


def test_slow_batch_falls_back_alone_within_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rerank, "BATCH_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(rerank, "DEADLINE_SLACK_SEC", 0.05)
    records, _ = synthetic()
    slow_first = records[1]["clip_id"]  # batch 1 starts with clip 1 (round-robin)

    def delay(prompt: str) -> float:
        return 2.0 if prompt_clips(prompt)[0]["clip_id"] == slow_first else 0.0

    reply = scored_reply(score=lambda i, c: 0.9 - i / 10)
    verdict, elapsed = judge(SleepyLLM(reply, delay))
    assert elapsed < 1.0  # bounded by the deadline, not by the slow call
    assert [b.outcome for b in verdict.batches] == ["ok", "timeout", "ok"]
    slow_ids = set(verdict.batches[1].clip_ids)
    assert len(slow_ids) == 6 and verdict.stats["imputed"] == 6
    answered = [j.score for cid, j in verdict.judged.items() if cid not in slow_ids]
    for clip_id, judgement in verdict.judged.items():
        if clip_id in slow_ids:  # template line, placed at the median of the others
            assert judgement.why is None
            assert judgement.score == pytest.approx(statistics.median(answered))
        else:
            assert judgement.why and judgement.why.startswith("You run ")


def test_slow_batch_keeps_model_lines_for_the_rest_of_the_playlist(
    use_model, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rerank, "BATCH_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(rerank, "DEADLINE_SLACK_SEC", 0.05)
    model = SlowFirstLLM(scored_reply(), delay=2.0)
    data = playlist(use_model(model))
    timings = data["debug"]["timings_ms"]
    batches = sum(1 for k in timings if k.startswith("rerank_batch_"))
    assert batches == len(model.calls) + 1 > 1  # the slow call hasn't returned yet
    assert data["debug"]["rerank"]["model"] == f"partial: 1/{batches} batches fell back (timeout)"
    assert timings["rerank"] < 1000
    slow_ids = {c["clip_id"] for c in prompt_clips(model.slow_prompt or "")}
    shown = data["clips"]
    assert any(c["clip_id"] in slow_ids for c in shown)  # its clips still play
    assert any(c["why"].startswith("You run ") for c in shown)  # the others keep model lines
    for clip in shown:
        if clip["clip_id"] in slow_ids:
            assert clip["why"].startswith(TEMPLATE_STARTS), clip
        elif clip["matched_tools"]:
            assert clip["why"].startswith("You run "), clip


def test_every_batch_late_returns_elastic_order_in_bounded_time(
    use_model, fake: FakeSearchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rerank, "BATCH_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(rerank, "DEADLINE_SLACK_SEC", 0.05)
    started = time.perf_counter()
    data = playlist(use_model(SleepyLLM(scored_reply(), lambda prompt: 2.0)))
    assert time.perf_counter() - started < 1.5
    assert data["debug"]["rerank"]["model"] == "fallback: LLMError"
    assert data["debug"]["timings_ms"]["rerank"] < 800
    assert [c["clip_id"] for c in data["clips"]] == elastic_order(fake)


# --- api/llm.py: no SDK retries, warm client, token metric ----------------------------


class FakeAnthropicSDK:
    """Stands in for `anthropic.Anthropic`: records options and request arguments."""

    def __init__(self) -> None:
        self.options: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def with_options(self, **options: Any) -> "FakeAnthropicSDK":
        self.options.append(options)
        return self

    def create(self, **params: Any) -> Any:
        self.requests.append(params)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="[]")],
            usage=SimpleNamespace(input_tokens=50, output_tokens=20),
        )


def test_anthropic_calls_never_retry_and_rerank_skips_thinking_on_haiku(
    spans: InMemorySpanExporter,
) -> None:
    backend = llm.AnthropicBackend("test-key-not-real")
    sdk = FakeAnthropicSDK()
    backend._sdk = sdk
    client = llm.LLM(backend, {"rerank": "claude-haiku-5-5", "memo": "claude-sonnet-5-5"})
    client.complete("p", purpose="rerank", max_tokens=500, json=True, timeout=6.0)
    client.complete("p", purpose="memo", max_tokens=4000, json=True, timeout=45.0)
    assert sdk.options == [{"timeout": 6.0, "max_retries": 0}, {"timeout": 45.0, "max_retries": 0}]
    rerank_req, memo_req = sdk.requests
    assert rerank_req["model"] == "claude-haiku-5-5" and rerank_req["max_tokens"] == 500
    assert rerank_req["thinking"] == {"type": "disabled"}
    assert rerank_req["output_config"] == {"effort": "low"}
    assert "fallbacks" not in rerank_req and "betas" not in rerank_req  # none on Haiku
    assert "thinking" not in memo_req and memo_req["fallbacks"] == "default"
    prepare = [s for s in spans.get_finished_spans() if s.name == "llm.prepare"]
    complete = [s for s in spans.get_finished_spans() if s.name == "llm.complete"]
    assert len(prepare) == 2 and prepare[0].parent.span_id == complete[0].context.span_id
    assert prepare[0].attributes["debrief.llm.client_cached"] is True


def test_sdk_client_is_built_once_at_warm_up_without_retries() -> None:
    llm.anthropic_sdk.cache_clear()
    backend = llm.AnthropicBackend("test-key-not-real")
    llm.LLM(backend, {"rerank": "claude-haiku-5-5"}).warm()  # builds it; no network
    assert backend._sdk is not None and backend._sdk.max_retries == 0
    assert llm.AnthropicBackend("test-key-not-real")._client() is backend._sdk  # shared
    llm.anthropic_sdk.cache_clear()
    llm.LLM(RaisingBackend(LLMError("x")), {}).warm()  # no warm(): logged, never raised
    llm.build_llm({"LLM_PROVIDER": "anthropic"}).warm()  # no key: nothing to build


def test_api_startup_warms_the_model_client(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(llm, "warm", lambda: calls.append("warm"))
    with TestClient(app) as api:
        assert api.get("/healthz").status_code == 200
    assert calls == ["warm"]


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("debrief")
    monkeypatch.setattr(telemetry, "instruments", telemetry.Instruments(meter))
    return reader


def metric_points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    data = reader.get_metrics_data()
    return [
        point
        for rm in (data.resource_metrics if data else [])
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def test_every_model_call_records_its_tokens(reader: InMemoryMetricReader) -> None:
    client = llm.LLM(EchoBackend(), {"rerank": "m", "memo": "m"})
    client.complete("p", purpose="rerank", max_tokens=10)
    client.complete("p", purpose="rerank", max_tokens=10)
    tokens = {
        (p.attributes["purpose"], p.attributes["provider"], p.attributes["token_type"]): p.value
        for p in metric_points(reader, "debrief.llm.tokens")
    }
    assert tokens == {("rerank", "fake", "input"): 22, ("rerank", "fake", "output"): 14}


def test_memo_dropped_lines_are_counted_by_reason(use_model, reader: InMemoryMetricReader) -> None:
    api = use_model(None)
    ids = clip_ids_for(api)
    response = use_model(FakeLLM(memo_reply(ids))).post(
        "/api/memo", json={"tools": GITOPS["tools"], "clip_ids": ids}
    )
    assert response.status_code == 200
    dropped = {
        p.attributes["reason"]: p.value
        for p in metric_points(reader, "debrief.memo.citations_dropped")
    }
    # "Invented advice", "Half-invented step" and "Does not exist" cite unknown ids;
    # "Uncited opinion" cites nothing.
    assert dropped == {"unknown_clip": 3, "uncited": 1}
