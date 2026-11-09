import csv
import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from api.llm import LLMError, LLMResult
from ingest import label as lb


def _chunks(talk_id: str, duration: int, words_per_sec: int = 1) -> list[dict[str, Any]]:
    """75 s chunks with 15 s overlap; the words of second s are "s{s}" (repeated per rate)."""
    chunks = []
    start = 0
    while start < duration:
        end = min(duration, start + 75)
        text = " ".join(f"s{s}" for s in range(start, end) for _ in range(words_per_sec))
        chunks.append(
            {
                "chunk_id": f"{talk_id}-{start}",
                "talk_id": talk_id,
                "title": f"Talk {talk_id}",
                "start_sec": start,
                "end_sec": end,
                "text": text,
                "tools": [],
                "talk_label": None,
            }
        )
        if end == duration:
            break
        start = end - 15
    return chunks


def _seconds(text: str) -> list[int]:
    return [int(w[1:]) for w in text.split()]


class FakeLLM:
    """Scripted replies, popped in order; records every call's purpose and timeout."""

    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def complete(
        self,
        prompt: str,
        *,
        purpose: str,
        max_tokens: int,
        json: bool = False,
        timeout: float | None = None,
        schema: Any = None,
    ) -> LLMResult:
        with self.lock:
            self.calls.append({"purpose": purpose, "json": json, "timeout": timeout})
            reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResult(reply, "fake", "fake-fast", 100, 20)


def _reply(label: str = "deep_dive", confidence: float = 0.9, reason: str = "numbers") -> str:
    return json.dumps({"label": label, "confidence": confidence, "reason": reason})


# --- Sampling ---------------------------------------------------------------------------


def test_timeline_drops_the_overlap_between_chunks() -> None:
    words = [w for _, w in lb.timeline(_chunks("t", 300))]
    assert words == [f"s{s}" for s in range(300)]


def test_sample_picks_opening_middle_and_end_windows() -> None:
    sections = lb.sample_talk(_chunks("t", 1800))
    assert [s.name for s in sections] == ["opening", "middle", "end"]
    opening, middle, end = (_seconds(s.text) for s in sections)
    assert opening == list(range(0, 120))
    assert middle == list(range(840, 960))
    assert end == list(range(1740, 1800))


def test_sample_windows_never_overlap_on_a_short_talk() -> None:
    sections = lb.sample_talk(_chunks("t", 200))
    seen = [s for section in sections for s in _seconds(section.text)]
    assert seen == list(range(200))  # every second once, in order


def test_sample_respects_the_word_cap() -> None:
    sections = lb.sample_talk(_chunks("t", 1800, words_per_sec=10))  # 3,000 words in windows
    assert sum(s.words for s in sections) <= lb.MAX_WORDS
    assert [s.name for s in sections] == ["opening", "middle", "end"]
    assert _seconds(sections[0].text)[0] == 0  # the opening keeps its first words
    assert _seconds(sections[2].text)[-1] == 1799  # the end keeps its last words
    assert sum(s.words for s in lb.sample_talk(_chunks("t", 1800), max_words=100)) <= 100


def test_prompt_has_the_excerpts_but_not_the_title() -> None:
    chunks = _chunks("t", 600)
    prompt = lb.build_prompt(lb.sample_talk(chunks), 600)
    assert "[opening, 0:00-2:00]" in prompt and "[end, 9:00-10:00]" in prompt
    assert "Talk t" not in prompt and "10 minutes" in prompt


# --- The model call ---------------------------------------------------------------------


def test_parse_label_normalises_and_trims() -> None:
    reply = '```json\n{"label": "Deep Dive", "confidence": 1.7, "reason": "' + "w " * 30 + '"}\n```'
    parsed = lb.parse_label("t", reply)
    assert parsed.label == "deep_dive" and parsed.confidence == 1.0
    assert len(parsed.reason.split()) == lb.MAX_REASON_WORDS


@pytest.mark.parametrize(
    "reply", ["not json", '{"label": "keynote", "confidence": 0.5}', '{"label": "intro"}', "[1]"]
)
def test_parse_label_rejects_bad_replies(reply: str) -> None:
    with pytest.raises(ValueError):
        lb.parse_label("t", reply)


def test_bad_json_is_retried_once_then_succeeds() -> None:
    llm = FakeLLM(["sure! here you go", _reply("pitch", 0.8, "promotes a vendor")])
    result = lb.label_talk(llm, "t", _chunks("t", 600))
    assert result == lb.TalkLabel("t", "pitch", 0.8, "promotes a vendor")
    assert len(llm.calls) == 2
    assert all(c["purpose"] == "label" and c["json"] for c in llm.calls)


def test_bad_label_twice_fails_cleanly() -> None:
    llm = FakeLLM([_reply("keynote"), _reply("product")])
    with pytest.raises(lb.LabelError, match="t: label not one of"):
        lb.label_talk(llm, "t", _chunks("t", 600))
    assert len(llm.calls) == 2


def test_model_error_is_retried_once() -> None:
    llm = FakeLLM([LLMError("anthropic call failed (APITimeoutError)"), _reply("intro")])
    assert lb.label_talk(llm, "t", _chunks("t", 600), timeout=5).label == "intro"
    assert [c["timeout"] for c in llm.calls] == [5, 5]


def test_label_talks_reports_progress_and_failures() -> None:
    talks = {f"t{i}": _chunks(f"t{i}", 300) for i in range(4)}
    llm = FakeLLM([_reply()] * 3 + ["bad", "bad"])
    progress: list[tuple[str, int, int, bool]] = []
    labels, failures = lb.label_talks(llm, talks, workers=1, progress=lambda *a: progress.append(a))
    assert len(labels) == 3 and len(failures) == 1
    assert [p[1:3] for p in progress] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert sum(1 for p in progress if not p[3]) == 1


# --- The labels file --------------------------------------------------------------------


def test_csv_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "talk_labels.csv"
    labels = [
        lb.TalkLabel("a", "deep_dive", 0.9, 'benchmarks, trade-offs, and "numbers"'),
        lb.TalkLabel("b", "pitch", 0.75, "sponsored keynote"),
    ]
    lb.write_labels_csv(path, labels)
    assert list(lb.read_labels_csv(path).values()) == labels
    with path.open(newline="") as f:
        assert next(csv.reader(f)) == ["talk_id", "label", "confidence", "reason"]


def test_csv_rejects_an_unknown_label(tmp_path: Path) -> None:
    path = tmp_path / "talk_labels.csv"
    path.write_text("talk_id,label,confidence,reason\na,keynote,0.5,x\n")
    with pytest.raises(ValueError, match="talk_labels.csv:2"):
        lb.read_labels_csv(path)


# --- Applying the labels ----------------------------------------------------------------


class FakeIndices:
    def __init__(self) -> None:
        self.refreshed: list[str] = []

    def refresh(self, index: str) -> dict[str, Any]:
        self.refreshed.append(index)
        return {}

    def create(self, **_: Any) -> None:
        raise AssertionError("label.py must never create an index")

    def delete(self, **_: Any) -> None:
        raise AssertionError("label.py must never delete an index")


class FakeES:
    """Applies bulk partial updates to an in-memory store, like Elastic's noop detection."""

    def __init__(
        self, docs: dict[str, dict[str, dict[str, Any]]], statuses: Sequence[int] = ()
    ) -> None:
        self.docs = docs
        self.indices = FakeIndices()
        self.bulk_calls: list[list[dict[str, Any]]] = []
        self.statuses = list(statuses)

    def bulk(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        self.bulk_calls.append(operations)
        items = []
        for action, body in zip(operations[::2], operations[1::2], strict=True):
            meta = action["update"]
            status = self.statuses.pop(0) if self.statuses else 200
            doc = self.docs.get(meta["_index"], {}).get(meta["_id"])
            if doc is None:
                status = 404
            item: dict[str, Any] = {"_id": meta["_id"], "status": status}
            if status >= 300:
                item["error"] = {"type": "document_missing_exception", "reason": "missing"}
            elif all(doc.get(k) == v for k, v in body["doc"].items()):
                item["result"] = "noop"
            else:
                doc.update(body["doc"])
                item["result"] = "updated"
            items.append({"update": item})
        return {"errors": any(i["update"]["status"] >= 300 for i in items), "items": items}

    def count(self, index: str, query: dict[str, Any]) -> dict[str, int]:
        return {"count": sum(1 for d in self.docs[index].values() if not d.get("talk_label"))}


def _store(chunks: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "clips": {c["chunk_id"]: {"talk_label": None} for c in chunks},
        "talks": {c["talk_id"]: {"talk_label": None} for c in chunks},
    }


def test_update_actions_cover_every_talk_and_clip() -> None:
    chunks = _chunks("a", 135) + _chunks("b", 135) + _chunks("c", 80)
    labels = {"a": lb.TalkLabel("a", "pitch", 0.9, ""), "b": lb.TalkLabel("b", "intro", 0.6, "")}
    assert lb.update_actions(chunks, labels) == [
        ("talks", "a", "pitch"),
        ("talks", "b", "intro"),
        ("clips", "a-0", "pitch"),
        ("clips", "a-60", "pitch"),
        ("clips", "b-0", "intro"),
        ("clips", "b-60", "intro"),
    ]


def test_apply_to_elastic_sends_partial_updates_and_is_idempotent() -> None:
    chunks = _chunks("a", 135)
    es = FakeES(_store(chunks))
    actions = lb.update_actions(chunks, {"a": lb.TalkLabel("a", "deep_dive", 0.9, "")})
    results, failures = lb.apply_to_elastic(es, actions, batch_size=2)
    assert failures == [] and results == {"updated": 3}
    assert es.bulk_calls[0][:2] == [
        {"update": {"_index": "talks", "_id": "a"}},
        {"doc": {"talk_label": "deep_dive"}},
    ]
    assert len(es.bulk_calls) == 2  # 3 docs in batches of 2
    results, _ = lb.apply_to_elastic(es, actions)
    assert results == {"noop": 3}


def test_apply_to_elastic_retries_429_and_reports_missing_docs() -> None:
    chunks = _chunks("a", 135)
    store = _store(chunks)
    del store["clips"]["a-60"]
    es = FakeES(store, statuses=[429, 200, 200, 200])
    actions = lb.update_actions(chunks, {"a": lb.TalkLabel("a", "intro", 0.9, "")})
    results, failures = lb.apply_to_elastic(es, actions, sleep=lambda _: None)
    assert results == {"updated": 2}
    assert len(failures) == 1 and failures[0].startswith("clips/a-60: status 404")


# --- The whole run ----------------------------------------------------------------------


def _write_chunks(path: Path, chunks: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(c) + "\n" for c in chunks))


def test_run_skips_talks_already_labelled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chunks = _chunks("a", 135) + _chunks("b", 135)
    chunks_path, labels_path = tmp_path / "chunks.jsonl", tmp_path / "talk_labels.csv"
    _write_chunks(chunks_path, chunks)
    lb.write_labels_csv(labels_path, [lb.TalkLabel("a", "pitch", 0.95, "sponsored")])
    llm = FakeLLM([_reply("deep_dive", 0.8, "benchmarks")])
    es = FakeES(_store(chunks))

    code = lb.run(chunks_path, labels_path, llm=llm, client=es, progress=None)

    assert code == 0 and len(llm.calls) == 1  # only talk b went to the model
    assert {t: v.label for t, v in lb.read_labels_csv(labels_path).items()} == {
        "a": "pitch",
        "b": "deep_dive",
    }
    labelled = [json.loads(line) for line in chunks_path.read_text().splitlines()]
    assert {c["chunk_id"]: c["talk_label"] for c in labelled} == {
        "a-0": "pitch",
        "a-60": "pitch",
        "b-0": "deep_dive",
        "b-60": "deep_dive",
    }
    assert all(d["talk_label"] for docs in es.docs.values() for d in docs.values())
    out = capsys.readouterr().out
    assert "Talk a" in out and "sponsored" in out and "2 of 2 talks labelled" in out
    assert "pitch        1" in out and "deep_dive    1" in out

    # A second run asks the model nothing and changes nothing in Elastic.
    assert lb.run(chunks_path, labels_path, llm=FakeLLM([]), client=es, progress=None) == 0
    assert "6 docs sent; noop 6; 0 failed" in capsys.readouterr().err


def test_run_relabel_asks_again_and_a_failure_exits_1(tmp_path: Path) -> None:
    chunks = _chunks("a", 135)
    chunks_path, labels_path = tmp_path / "chunks.jsonl", tmp_path / "talk_labels.csv"
    _write_chunks(chunks_path, chunks)
    lb.write_labels_csv(labels_path, [lb.TalkLabel("a", "pitch", 0.95, "sponsored")])

    llm = FakeLLM([_reply("intro", 0.7, "basics")])
    assert lb.run(chunks_path, labels_path, llm=llm, relabel=True, progress=None) == 0
    assert lb.read_labels_csv(labels_path)["a"].label == "intro"

    labels_path.unlink()
    assert lb.run(chunks_path, labels_path, llm=FakeLLM(["x", "y"]), progress=None) == 1
    assert not labels_path.exists()


# --- Telemetry --------------------------------------------------------------------------


def test_run_is_one_trace_with_a_span_per_talk(tmp_path: Path) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import api.main  # noqa: F401  installs the SDK tracer provider, as the other tests do

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    spans = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(spans))

    chunks = _chunks("a", 135) + _chunks("b", 135)
    chunks_path, labels_path = tmp_path / "chunks.jsonl", tmp_path / "talk_labels.csv"
    _write_chunks(chunks_path, chunks)
    llm = FakeLLM([_reply("intro", 0.7), _reply("intro", 0.7)])
    assert lb.run(chunks_path, labels_path, llm=llm, workers=2, progress=None) == 0

    finished = spans.get_finished_spans()
    spans.shutdown()
    root = next(s for s in finished if s.name == "ingest.label")
    talks = [s for s in finished if s.name == "ingest.label.talk"]
    assert root.parent is None and root.attributes["debrief.labels.intro"] == 2
    assert len(talks) == 2  # worker threads still parent their span on the root
    assert all(s.parent and s.parent.span_id == root.context.span_id for s in talks)
    assert {s.attributes["debrief.talk_id"] for s in talks} == {"a", "b"}
    assert all(s.attributes["debrief.talk_label"] == "intro" for s in talks)
    assert all(s.attributes["debrief.label_confidence"] == 0.7 for s in talks)
    assert all(s.attributes["gen_ai.usage.input_tokens"] == 100 for s in talks)
    for span in finished:  # ids, labels and counts only: never transcript words
        for value in (span.attributes or {}).values():
            assert "s10 s11" not in str(value), (span.name, value)
