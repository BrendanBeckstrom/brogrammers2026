"""Task 12: label each talk deep dive, intro or pitch from what its speakers said.

    python -m ingest.label [--chunks data/chunks.jsonl] [--labels data/talk_labels.csv]
                           [--relabel] [--no-elastic] [--workers 6] [--timeout 30]

For each talk it samples the cleaned transcript (the opening ~2 minutes, ~2 minutes from
the middle and the last minute, capped at ~1,500 words; never the abstract) and asks the
fast model, through `api/llm.py` with `purpose="label"`, for
`{"label", "confidence", "reason"}`. A reply with bad JSON or a label outside
`deep_dive` | `intro` | `pitch` is retried once; a second failure leaves the talk
unlabelled and the run exits 1.

Labels are kept in `data/talk_labels.csv` (committed, so they can be reviewed and a
re-ingest is deterministic). A talk already in that file is not sent to the model again
unless `--relabel` is passed. The labels are then applied:

- `talk_label` on every line of the chunks file (rewritten atomically);
- `talk_label` on every `clips` doc (bulk partial updates by chunk id) and every `talks`
  doc (by talk id) in Elastic, in place. The indexes are never recreated, and an
  unchanged label is a no-op, so a re-run is safe while the app reads them.
  `--no-elastic` skips this step (`make ingest` does; `make index` loads the labels from
  the chunks file).

Finally it prints every talk's title, label, confidence and reason for a person to check,
then the count per label. Progress lines, spans and logs carry ids, labels, confidences,
counts and token totals only, never transcript text or prompts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace

from api import telemetry
from api.llm import LLMClient, LLMError, get_llm
from ingest.tag import read_jsonl, write_jsonl

LABELS = ("deep_dive", "intro", "pitch")
DEFAULT_CHUNKS = Path("data/chunks.jsonl")
DEFAULT_LABELS_CSV = Path("data/talk_labels.csv")
CSV_FIELDS = ("talk_id", "label", "confidence", "reason")
CLIPS_INDEX = "clips"
TALKS_INDEX = "talks"

OPENING_SEC = 120.0
MIDDLE_SEC = 120.0
END_SEC = 60.0
MAX_WORDS = 1500
MAX_OVERLAP_WORDS = 400  # neighbouring chunks share ~15 s of text; never more than this
MAX_REASON_WORDS = 15

WORKERS = 6
CALL_TIMEOUT_SEC = 30.0
MAX_TOKENS = 1024  # the reply is ~60 tokens; headroom for the model's own reasoning
ATTEMPTS = 2  # the first try plus one retry

UPDATE_BATCH_SIZE = 200
MAX_UPDATE_ATTEMPTS = 4
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
TITLE_WIDTH = 60

PROMPT = """Classify one conference talk from what its speakers actually said. You get \
excerpts of its transcript (automatic captions), not the abstract: judge only from them.

Labels:
- deep_dive: technical depth: real implementation detail, numbers from running it, \
benchmarks, trade-offs, lessons learned
- intro: an overview or getting-started material for newcomers to a tool or topic
- pitch: mainly serves a company's marketing: promotes its product, service, platform or \
its own report (a vendor talk or product demo). An open source project's own update or \
roadmap is not a pitch: judge its depth instead.

Talk length: {minutes} minutes.

{excerpts}

Reply with one JSON object: {{"label": "deep_dive" | "intro" | "pitch", "confidence": a \
number from 0 to 1, "reason": "at most 12 words on what in the transcript decided it"}}"""

RETRY_NOTE = (
    '\n\nYour previous reply was not one JSON object with "label" set to deep_dive, intro '
    "or pitch. Reply again with only that object."
)

Chunk = dict[str, Any]
Progress = Callable[[str, int, int, bool], None]

tracer = trace.get_tracer("debrief")


class LabelError(RuntimeError):
    """A talk could not be labelled. The message names the failure, never model text."""


@dataclass(frozen=True)
class Section:
    """One stretch of transcript sent to the model."""

    name: str
    start_sec: float
    end_sec: float
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class TalkLabel:
    talk_id: str
    label: str
    confidence: float
    reason: str


# --- Sampling ---------------------------------------------------------------------------


def _overlap(previous: Sequence[str], following: Sequence[str]) -> int:
    """Length of the longest tail of `previous` that `following` starts with."""
    for k in range(min(len(previous), len(following), MAX_OVERLAP_WORDS), 0, -1):
        if previous[-k:] == following[:k]:
            return k
    return 0


def timeline(chunks: Iterable[Chunk]) -> list[tuple[float, str]]:
    """Every word of a talk once, with an estimated time, from its overlapping chunks.

    Neighbouring chunks repeat ~15 seconds of sentences, so the words a chunk shares
    with the one before it are dropped. A word's time is interpolated linearly across
    its chunk.
    """
    words: list[tuple[float, str]] = []
    previous: list[str] = []
    for chunk in sorted(chunks, key=lambda c: (c["start_sec"], c["end_sec"])):
        tokens = str(chunk.get("text") or "").split()
        if not tokens:
            continue
        start, end = float(chunk["start_sec"]), float(chunk["end_sec"])
        step = max(end - start, 0.0) / len(tokens)
        skip = _overlap(previous, tokens)
        words += [(start + i * step, tokens[i]) for i in range(skip, len(tokens))]
        previous = tokens
    return words


def _windows(first: float, last: float) -> list[tuple[str, float, float]]:
    """The opening, middle and end windows of a talk, never overlapping."""
    opening = (first, min(last, first + OPENING_SEC))
    middle_start = max(opening[1], first + (last - first) / 2 - MIDDLE_SEC / 2)
    middle = (middle_start, min(last, middle_start + MIDDLE_SEC))
    end = (max(middle[1], last - END_SEC), last)
    return [("opening", *opening), ("middle", *middle), ("end", *end)]


def sample_talk(chunks: Sequence[Chunk], max_words: int = MAX_WORDS) -> list[Section]:
    """The transcript excerpts the model sees for one talk, at most `max_words` in all.

    When the windows hold more than the cap, each is cut in proportion to its size:
    the opening and middle keep their first words, the end keeps its last.
    """
    words = timeline(chunks)
    if not words:
        return []
    first = float(min(c["start_sec"] for c in chunks))
    last = float(max(c["end_sec"] for c in chunks))
    picked: list[tuple[str, float, float, list[str]]] = []
    for name, start, end in _windows(first, last):
        if end <= start:
            continue
        inside = [w for t, w in words if start <= t < end]
        if inside:
            picked.append((name, start, end, inside))
    total = sum(len(w) for *_, w in picked)
    sections = []
    for name, start, end, inside in picked:
        if total > max_words:
            keep = max_words * len(inside) // total
            inside = inside[-keep:] if name == "end" and keep else inside[:keep]
        sections.append(Section(name, start, end, " ".join(inside)))
    return [s for s in sections if s.text]


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def build_prompt(sections: Sequence[Section], duration_sec: float) -> str:
    excerpts = "\n\n".join(
        f"[{s.name}, {_clock(s.start_sec)}-{_clock(s.end_sec)}]\n{s.text}" for s in sections
    )
    return PROMPT.format(minutes=max(1, round(duration_sec / 60)), excerpts=excerpts)


# --- The model call ---------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_label(talk_id: str, text: str) -> TalkLabel:
    """Validate one model reply. Raises ValueError on bad JSON or a label not allowed."""
    raw = _FENCE.sub("", text.strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"bad JSON ({e.msg})") from e
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    label = re.sub(r"[\s\-]+", "_", str(data.get("label", "")).strip().lower())
    if label not in LABELS:
        raise ValueError("label not one of " + ", ".join(LABELS))
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError) as e:
        raise ValueError("confidence is not a number") from e
    if confidence != confidence:  # NaN
        raise ValueError("confidence is not a number")
    confidence = min(1.0, max(0.0, confidence))
    words = str(data.get("reason") or "").split()
    reason = " ".join(words[:MAX_REASON_WORDS])
    if len(words) > MAX_REASON_WORDS:
        reason = reason.rstrip(",;:.-–—(") + "…"
    return TalkLabel(talk_id, label, round(confidence, 2), reason)


def label_talk(
    llm: LLMClient,
    talk_id: str,
    chunks: Sequence[Chunk],
    timeout: float = CALL_TIMEOUT_SEC,
) -> TalkLabel:
    """Ask the model for one talk's label, retrying once on a bad reply or failed call.

    Runs in one `ingest.label.talk` span carrying the talk id, label, confidence,
    attempts and token totals.
    """
    with tracer.start_as_current_span("ingest.label.talk") as span:
        span.set_attribute("debrief.talk_id", talk_id)
        sections = sample_talk(chunks)
        if not sections:
            span.set_attribute("error.type", "empty_transcript")
            raise LabelError(f"{talk_id}: no transcript text to sample")
        duration = max(c["end_sec"] for c in chunks) - min(c["start_sec"] for c in chunks)
        prompt = build_prompt(sections, duration)
        span.set_attribute("debrief.sample_words", sum(s.words for s in sections))
        problem = ""
        tokens_in = tokens_out = 0
        for attempt in range(1, ATTEMPTS + 1):
            span.set_attribute("debrief.attempts", attempt)
            try:
                result = llm.complete(
                    prompt + (RETRY_NOTE if attempt > 1 else ""),
                    purpose="label",
                    max_tokens=MAX_TOKENS,
                    json=True,
                    timeout=timeout,
                )
            except LLMError as e:
                problem = f"model call failed ({e})"
                continue
            tokens_in += result.input_tokens or 0
            tokens_out += result.output_tokens or 0
            if result.input_tokens is not None or result.output_tokens is not None:
                telemetry.record_llm_tokens(
                    "label", result.provider, result.input_tokens or 0, result.output_tokens or 0
                )
            try:
                labelled = parse_label(talk_id, result.text)
            except ValueError as e:
                problem = str(e)
                continue
            span.set_attributes(
                {
                    "debrief.talk_label": labelled.label,
                    "debrief.label_confidence": labelled.confidence,
                    "gen_ai.usage.input_tokens": tokens_in,
                    "gen_ai.usage.output_tokens": tokens_out,
                }
            )
            return labelled
        span.set_attribute("error.type", "bad_reply")
        span.set_status(trace.Status(trace.StatusCode.ERROR, "bad_reply"))
        raise LabelError(f"{talk_id}: {problem} after {ATTEMPTS} attempts")


def label_talks(
    llm: LLMClient,
    talks: Mapping[str, Sequence[Chunk]],
    workers: int = WORKERS,
    timeout: float = CALL_TIMEOUT_SEC,
    progress: Progress | None = None,
) -> tuple[dict[str, TalkLabel], list[str]]:
    """Label talks concurrently. Returns the labels by talk id and one message per failure."""
    labels: dict[str, TalkLabel] = {}
    failures: list[str] = []
    if not talks:
        return labels, failures
    parent = otel_context.get_current()  # worker threads don't inherit the root span

    def work(talk_id: str) -> TalkLabel:
        token = otel_context.attach(parent)
        try:
            return label_talk(llm, talk_id, talks[talk_id], timeout)
        finally:
            otel_context.detach(token)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, talk_id): talk_id for talk_id in talks}
        for done, future in enumerate(as_completed(futures), start=1):
            talk_id = futures[future]
            try:
                labels[talk_id] = future.result()
                ok = True
            except LabelError as e:
                failures.append(str(e))
                ok = False
            if progress:
                progress(talk_id, done, len(talks), ok)
    return labels, failures


# --- The labels file --------------------------------------------------------------------


def read_labels_csv(path: Path) -> dict[str, TalkLabel]:
    """Labels already decided, by talk id. A missing file means none."""
    if not path.exists():
        return {}
    labels: dict[str, TalkLabel] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            label = (row.get("label") or "").strip()
            talk_id = (row.get("talk_id") or "").strip()
            if not talk_id or label not in LABELS:
                raise ValueError(f"{path}:{line_no}: needs a talk_id and one of {LABELS}")
            labels[talk_id] = TalkLabel(
                talk_id, label, float(row.get("confidence") or 0), (row.get("reason") or "")
            )
    return labels


def write_labels_csv(path: Path, labels: Iterable[TalkLabel]) -> None:
    """Write the labels file atomically, in the order given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(CSV_FIELDS)
        for t in labels:
            writer.writerow([t.talk_id, t.label, f"{t.confidence:.2f}", t.reason])
    os.replace(tmp, path)


# --- Applying the labels ----------------------------------------------------------------


def group_by_talk(chunks: Iterable[Chunk]) -> dict[str, list[Chunk]]:
    """Chunks per talk id, talks in first-seen order."""
    talks: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        talks.setdefault(chunk["talk_id"], []).append(chunk)
    return talks


def apply_to_chunks(chunks: Iterable[Chunk], labels: Mapping[str, TalkLabel]) -> list[Chunk]:
    """New chunk dicts with `talk_label` set from `labels`; unlabelled talks keep theirs."""
    out = []
    for chunk in chunks:
        labelled = labels.get(chunk["talk_id"])
        out.append({**chunk, "talk_label": labelled.label} if labelled else dict(chunk))
    return out


def update_actions(
    chunks: Iterable[Chunk], labels: Mapping[str, TalkLabel]
) -> list[tuple[str, str, str]]:
    """`(index, doc id, label)` for every `talks` doc, then every `clips` doc, to update."""
    talk_ids: dict[str, None] = {}
    clips: list[tuple[str, str, str]] = []
    for chunk in chunks:
        labelled = labels.get(chunk["talk_id"])
        if labelled is None:
            continue
        talk_ids[chunk["talk_id"]] = None
        clips.append((CLIPS_INDEX, chunk["chunk_id"], labelled.label))
    talks = [(TALKS_INDEX, talk_id, labels[talk_id].label) for talk_id in talk_ids]
    return talks + clips


def _describe_error(doc_id: str, item: Mapping[str, Any]) -> str:
    error = item.get("error") or {}
    if not isinstance(error, dict):
        error = {"reason": str(error)}
    reason = str(error.get("reason", "")).split("Preview of field", 1)[0].strip()
    return f"{doc_id}: status {item.get('status')} {error.get('type', '?')}: {reason[:200]}"


def apply_to_elastic(
    client: Any,
    actions: Sequence[tuple[str, str, str]],
    batch_size: int = UPDATE_BATCH_SIZE,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Counter[str], list[str]]:
    """Set `talk_label` in place with bulk partial updates; never creates or drops an index.

    Returns the count per bulk result ("updated", "noop") and one message per doc that
    failed. Retryable statuses (429 and 5xx) are re-sent with backoff.
    """
    results: Counter[str] = Counter()
    failures: list[str] = []
    for start in range(0, len(actions), batch_size):
        pending = list(actions[start : start + batch_size])
        for attempt in range(1, MAX_UPDATE_ATTEMPTS + 1):
            operations: list[dict[str, Any]] = []
            for index, doc_id, label in pending:
                operations.append({"update": {"_index": index, "_id": doc_id}})
                operations.append({"doc": {"talk_label": label}})
            response = client.bulk(operations=operations)
            retry = []
            for action, entry in zip(pending, response["items"], strict=True):
                item = next(iter(entry.values()))
                status = int(item.get("status", 0))
                if status < 300:
                    results[str(item.get("result", "updated"))] += 1
                elif status in RETRYABLE_STATUSES and attempt < MAX_UPDATE_ATTEMPTS:
                    retry.append(action)
                else:
                    failures.append(f"{action[0]}/" + _describe_error(action[1], item))
            if not retry:
                break
            pending = retry
            sleep(2.0**attempt)
    return results, failures


def missing_label_counts(client: Any) -> dict[str, int]:
    """How many `clips` and `talks` docs have no `talk_label`, after a refresh."""
    client.indices.refresh(index=f"{CLIPS_INDEX},{TALKS_INDEX}")
    query = {"bool": {"must_not": {"exists": {"field": "talk_label"}}}}
    return {
        index: int(client.count(index=index, query=query)["count"])
        for index in (CLIPS_INDEX, TALKS_INDEX)
    }


# --- Report -----------------------------------------------------------------------------


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def format_report(titles: Mapping[str, str], labels: Mapping[str, TalkLabel]) -> str:
    """Every labelled talk (title, label, confidence, reason), then the count per label."""
    lines = [f"{'title':<{TITLE_WIDTH}}  {'label':<9}  conf  reason"]
    for talk_id, title in titles.items():
        t = labels.get(talk_id)
        if t is None:
            lines.append(f"{_clip(title, TITLE_WIDTH):<{TITLE_WIDTH}}  {'-':<9}     -  (no label)")
            continue
        lines.append(
            f"{_clip(title, TITLE_WIDTH):<{TITLE_WIDTH}}  {t.label:<9}  {t.confidence:.2f}  "
            f"{t.reason}"
        )
    counts = Counter(labels[t].label for t in titles if t in labels)
    lines.append("")
    lines += [f"{label:<9}  {counts[label]:>3}" for label in LABELS]
    lines.append(f"{sum(counts.values())} of {len(titles)} talks labelled")
    return "\n".join(lines)


# --- Run --------------------------------------------------------------------------------


def _print_progress(talk_id: str, done: int, total: int, ok: bool) -> None:
    print(f"[{done}/{total}] {talk_id} {'ok' if ok else 'failed'}", file=sys.stderr, flush=True)


def run(
    chunks_path: Path,
    labels_path: Path,
    llm: LLMClient | None = None,
    client: Any = None,
    relabel: bool = False,
    workers: int = WORKERS,
    timeout: float = CALL_TIMEOUT_SEC,
    progress: Progress | None = _print_progress,
) -> int:
    """Label every talk, write the CSV and chunks, update Elastic when `client` is given.

    `llm` is only built (from the environment) when some talk still needs a label.
    Returns a process exit code: 0 when every talk has a label and nothing failed.
    """
    with tracer.start_as_current_span("ingest.label") as span:
        chunks = read_jsonl(chunks_path)
        talks = group_by_talk(chunks)
        known = read_labels_csv(labels_path)
        todo = {t: cs for t, cs in talks.items() if relabel or t not in known}
        span.set_attributes(
            {
                "debrief.talks": len(talks),
                "debrief.chunks": len(chunks),
                "debrief.talks_to_label": len(todo),
                "debrief.relabel": relabel,
            }
        )
        print(
            f"{len(talks)} talks, {len(chunks)} chunks; {len(talks) - len(todo)} already "
            f"labelled in {labels_path}, {len(todo)} to label",
            file=sys.stderr,
            flush=True,
        )

        new: dict[str, TalkLabel] = {}
        failures: list[str] = []
        if todo:
            new, failures = label_talks(llm or get_llm(), todo, workers, timeout, progress)
        labels = {**known, **new}
        ordered = [labels[t] for t in talks if t in labels]
        ordered += [v for t, v in known.items() if t not in talks]  # keep rows for other talks
        if new:
            write_labels_csv(labels_path, ordered)
        write_jsonl(chunks_path, apply_to_chunks(chunks, labels))

        missing = [t for t in talks if t not in labels]
        counts = Counter(labels[t].label for t in talks if t in labels)
        span.set_attributes(
            {
                "debrief.labelled": len(new),
                "debrief.failed": len(failures),
                "debrief.unlabelled": len(missing),
                **{f"debrief.labels.{k}": counts[k] for k in LABELS},
            }
        )

        update_failures: list[str] = []
        if client is not None:
            actions = update_actions(chunks, labels)
            results, update_failures = apply_to_elastic(client, actions)
            gaps = missing_label_counts(client)
            span.set_attributes(
                {
                    "debrief.es_updated": results["updated"],
                    "debrief.es_noop": results["noop"],
                    "debrief.es_failed": len(update_failures),
                    "debrief.clips_missing_label": gaps[CLIPS_INDEX],
                    "debrief.talks_missing_label": gaps[TALKS_INDEX],
                }
            )
            print(
                f"elastic: {len(actions)} docs sent; "
                + ", ".join(f"{k} {v}" for k, v in sorted(results.items()))
                + f"; {len(update_failures)} failed; docs without talk_label: "
                f"clips {gaps[CLIPS_INDEX]}, talks {gaps[TALKS_INDEX]}",
                file=sys.stderr,
            )

        titles = {t: str(cs[0].get("title") or t) for t, cs in talks.items()}
        print(format_report(titles, labels), flush=True)
        for failure in (failures + update_failures)[:20]:
            print(f"failed: {failure}", file=sys.stderr)
        code = 0 if not (missing or failures or update_failures) else 1
        span.set_attribute("debrief.exit_code", code)
        return code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Label each talk deep_dive, intro or pitch.")
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path(os.environ.get("DEBRIEF_CHUNKS") or DEFAULT_CHUNKS),
        help="chunks JSONL to label in place (env DEBRIEF_CHUNKS, default %(default)s)",
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_CSV, help="labels CSV")
    parser.add_argument("--relabel", action="store_true", help="ask the model for every talk")
    parser.add_argument(
        "--no-elastic", action="store_true", help="only write the CSV and the chunks file"
    )
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--timeout", type=float, default=CALL_TIMEOUT_SEC, help="per call, s")
    args = parser.parse_args(argv)
    if not args.chunks.exists():
        print(f"error: {args.chunks} not found; run the chunk step first", file=sys.stderr)
        return 1
    telemetry.setup(telemetry.INGEST_SERVICE)
    try:
        client = None
        if not args.no_elastic:
            from ingest.index import client_from_env

            client = client_from_env()
        return run(
            args.chunks,
            args.labels,
            client=client,
            relabel=args.relabel,
            workers=args.workers,
            timeout=args.timeout,
        )
    finally:
        telemetry.flush()  # a short-lived job: export before the process exits


if __name__ == "__main__":
    raise SystemExit(main())
