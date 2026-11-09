"""Clip building (task 6): turn search hits into playlist clips.

Pure functions, no Elastic: group hits by talk, merge neighbouring chunks into clips of
60-240 seconds, pick the quote, write the template "why" line, fill the time budget and
build watch links. `watch_url()` is the only place a YouTube link is built.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_CHUNKS_PER_TALK = 3
MERGE_GAP_SEC = 20  # chunks this close (or overlapping) merge into one clip
PRE_ROLL_SEC = 2  # clips start two seconds before the first sentence
MIN_CLIP_SEC = 60
MAX_CLIP_SEC = 240
MAX_CLIPS = 15
MAX_UNMATCHED_SHARE = 0.2  # clips that name none of the stack's tools: at most 1 in 5
QUOTE_MAX_CHARS = 300
QUOTE_MAX_SENTENCES = 3
CLIP_ID_SEP = "~"  # never appears in a YouTube id ([A-Za-z0-9_-])

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_TAG = re.compile(r"</?em>")
_WORD_SPLIT = re.compile(r"[\s\-]+")
_CHUNK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}-\d{1,6}$")


@dataclass(frozen=True)
class Hit:
    """One chunk returned by search, with its fused score and which half found it."""

    chunk_id: str
    talk_id: str
    youtube_id: str
    title: str
    speakers: tuple[str, ...]
    start_sec: int
    end_sec: int
    text: str
    tools: tuple[str, ...]
    talk_label: str | None
    score: float
    matched_by: str = "keyword"  # "keyword" | "semantic" | "both"
    highlight: str | None = None

    @classmethod
    def from_source(
        cls,
        source: Mapping[str, Any],
        score: float = 0.0,
        matched_by: str = "keyword",
        highlight: str | None = None,
    ) -> Hit:
        speakers = source.get("speakers") or []
        if isinstance(speakers, str):
            speakers = [speakers]
        return cls(
            chunk_id=str(source["chunk_id"]),
            talk_id=str(source["talk_id"]),
            youtube_id=str(source.get("youtube_id") or source["talk_id"]),
            title=str(source.get("title") or ""),
            speakers=tuple(str(s) for s in speakers),
            start_sec=int(source["start_sec"]),
            end_sec=int(source["end_sec"]),
            text=str(source.get("text") or ""),
            tools=tuple(source.get("tools") or ()),
            talk_label=source.get("talk_label"),
            score=float(score),
            matched_by=matched_by,
            highlight=highlight,
        )


@dataclass(frozen=True)
class Clip:
    """One playlist entry: one to three chunks of the same talk, in time order."""

    chunks: tuple[Hit, ...]

    @property
    def first(self) -> Hit:
        return self.chunks[0]

    @property
    def best(self) -> Hit:
        """The chunk with the highest fused score; the quote comes from it."""
        return max(self.chunks, key=lambda c: c.score)

    @property
    def score(self) -> float:
        return self.best.score

    @property
    def clip_id(self) -> str:
        return make_clip_id(self.chunks)

    @property
    def bounds(self) -> tuple[int, int]:
        return clip_bounds(self.chunks)

    @property
    def length(self) -> int:
        start, end = self.bounds
        return end - start

    @property
    def tools(self) -> set[str]:
        return {t for c in self.chunks for t in c.tools}

    @property
    def matched_by(self) -> str:
        halves: set[str] = set()
        for c in self.chunks:
            halves.update(("keyword", "semantic") if c.matched_by == "both" else (c.matched_by,))
        return "both" if len(halves) > 1 else next(iter(halves), "keyword")

    @property
    def text(self) -> str:
        return merged_text(self.chunks)


def watch_url(youtube_id: str, start_sec: int) -> str:
    """The YouTube link that opens a video at `start_sec`. The only place it is built."""
    return f"https://www.youtube.com/watch?v={youtube_id}&t={int(start_sec)}s"


def make_clip_id(chunks: Sequence[Hit]) -> str:
    """The first chunk_id, then `~<start_sec>` for each further chunk of the same talk."""
    head, *rest = chunks
    return CLIP_ID_SEP.join([head.chunk_id, *(str(c.start_sec) for c in rest)])


def parse_clip_id(clip_id: str) -> list[str]:
    """The chunk ids a clip id (or a plain chunk id) stands for. Raises ValueError."""
    head, *rest = clip_id.split(CLIP_ID_SEP)
    if not _CHUNK_ID.match(head) or len(rest) >= MAX_CHUNKS_PER_TALK:
        raise ValueError("not a clip or chunk id")
    if not all(part.isdigit() and len(part) <= 6 for part in rest):
        raise ValueError("not a clip or chunk id")
    talk_id = head.rsplit("-", 1)[0]
    return [head, *(f"{talk_id}-{part}" for part in rest)]


def clip_bounds(chunks: Sequence[Hit]) -> tuple[int, int]:
    """Start two seconds before the first chunk; run 60 to 240 seconds."""
    start = max(0, min(c.start_sec for c in chunks) - PRE_ROLL_SEC)
    end = max(c.end_sec for c in chunks)
    end = min(end, start + MAX_CLIP_SEC)
    return start, max(end, start + MIN_CLIP_SEC)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def merged_text(chunks: Iterable[Hit]) -> str:
    """The chunks' text in time order, without the sentences repeated by their overlap."""
    seen: set[str] = set()
    out: list[str] = []
    for chunk in sorted(chunks, key=lambda c: c.start_sec):
        for sentence in split_sentences(chunk.text):
            if sentence not in seen:
                seen.add(sentence)
                out.append(sentence)
    return " ".join(out)


def group_by_talk(hits: Iterable[Hit], per_talk: int = MAX_CHUNKS_PER_TALK) -> list[list[Hit]]:
    """Hits grouped by talk_id, at most `per_talk` each, talks in order of their best hit."""
    groups: dict[str, list[Hit]] = {}
    for hit in hits:
        group = groups.setdefault(hit.talk_id, [])
        if len(group) < per_talk:
            group.append(hit)
    return list(groups.values())


def merge_talk(hits: Iterable[Hit]) -> list[Clip]:
    """Merge one talk's hits that overlap or sit within MERGE_GAP_SEC into clips.

    A chunk joins the previous clip only if all of it fits inside the MAX_CLIP_SEC cap.
    """
    groups: list[list[Hit]] = []
    for hit in sorted(hits, key=lambda h: h.start_sec):
        if groups:
            group = groups[-1]
            start, _ = clip_bounds(group)
            end = max(c.end_sec for c in group)
            near = hit.start_sec <= end + MERGE_GAP_SEC
            fits = hit.end_sec - start <= MAX_CLIP_SEC
            if near and fits:
                group.append(hit)
                continue
        groups.append([hit])
    return [Clip(tuple(group)) for group in groups]


def _score_order(clip: Clip) -> tuple[float, str]:
    return (-clip.score, clip.clip_id)


def build_clips(groups: Iterable[Sequence[Hit]]) -> list[Clip]:
    """Merge each talk's hits into clips; best score first."""
    return sorted((clip for talk in groups for clip in merge_talk(talk)), key=_score_order)


def fill_budget(
    clips: Iterable[Clip],
    budget_sec: int,
    requested_tools: Iterable[str],
    max_clips: int = MAX_CLIPS,
    max_unmatched_share: float = MAX_UNMATCHED_SHARE,
) -> list[Clip]:
    """Fill the budget in score order, skipping clips that would overflow it.

    Clips that mention a requested tool go first; clips found only by meaning then fill
    what is left, at most `max_unmatched_share` of the playlist. Returns score order.
    """
    wanted = set(requested_tools)
    ordered = sorted(clips, key=_score_order)
    chosen: list[Clip] = []
    total = 0
    for clip in ordered:
        if clip.tools & wanted and len(chosen) < max_clips and total + clip.length <= budget_sec:
            chosen.append(clip)
            total += clip.length
    unmatched = 0
    for clip in ordered:
        if clip.tools & wanted or len(chosen) >= max_clips:
            continue
        if unmatched + 1 > max_unmatched_share * (len(chosen) + 1):
            break
        if total + clip.length <= budget_sec:
            chosen.append(clip)
            total += clip.length
            unmatched += 1
    return sorted(chosen, key=_score_order)


def choose_quote(text: str, highlight: str | None, max_chars: int = QUOTE_MAX_CHARS) -> str:
    """Two or three sentences (at most `max_chars`) starting at the highlighted sentence.

    Falls back to the first sentences of `text` when there is no usable highlight.
    """
    sentences = split_sentences(text)
    if not sentences:
        return ""
    start = _sentence_at(text, sentences, highlight)
    chosen: list[str] = []
    for sentence in sentences[start : start + QUOTE_MAX_SENTENCES]:
        if len(" ".join([*chosen, sentence])) > max_chars:
            break
        chosen.append(sentence)
    if len(chosen) == 1 and start > 0:
        widened = [sentences[start - 1], *chosen]
        if len(" ".join(widened)) <= max_chars:
            chosen = widened
    if not chosen:
        return _truncate(sentences[start], max_chars)
    return " ".join(chosen)


def _sentence_at(text: str, sentences: Sequence[str], highlight: str | None) -> int:
    """Index of the sentence where the highlight fragment starts (0 if not found)."""
    fragment = _TAG.sub("", highlight or "").strip()
    if not fragment:
        return 0
    pos = text.find(fragment[:80])
    if pos < 0:
        return 0
    cursor = 0
    for i, sentence in enumerate(sentences):
        found = text.find(sentence, cursor)
        if found < 0:
            continue
        cursor = found + len(sentence)
        if pos < cursor:
            return i
    return 0


def _truncate(sentence: str, max_chars: int) -> str:
    if len(sentence) <= max_chars:
        return sentence
    cut = sentence[: max_chars - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;: ") + "…"


def _term_regex(term: str) -> str:
    words = [w for w in _WORD_SPLIT.split(term.strip().lower()) if w]
    return r"[\s\-]*".join(re.escape(w) for w in words)


def count_mentions(text: str, terms: Iterable[str]) -> int:
    """Whole-word, case-insensitive mentions of any term (same rules as ingest/tag.py)."""
    alternatives = sorted({r for r in map(_term_regex, terms) if r}, key=len, reverse=True)
    if not alternatives or not text:
        return 0
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)
    return len(pattern.findall(text))


def template_why(
    text: str,
    matched_tools: Sequence[str],
    names: Mapping[str, str],
    terms: Mapping[str, Sequence[str]],
    pain: str | None = None,
) -> str:
    """The non-model "why" line: names the most-mentioned matched tool and its count."""
    if matched_tools:
        counts = {
            t: count_mentions(text, [names.get(t, t), *terms.get(t, ())]) for t in matched_tools
        }
        top = max(matched_tools, key=lambda t: counts[t])
        name, n = names.get(top, top), counts[top]
        line = f"Mentions {name} {_times(n)} in this clip" if n else f"Tagged {name}"
        others = [names.get(t, t) for t in matched_tools if t != top]
        if others:
            line += f" (also {', '.join(others)})"
        return line
    if pain:
        return "Close in meaning to what hurts right now"
    return "Close in meaning to your stack"


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def clip_record(
    clip: Clip,
    requested_tools: Sequence[str],
    names: Mapping[str, str],
    terms: Mapping[str, Sequence[str]],
    pain: str | None = None,
) -> dict[str, Any]:
    """The clip shape from docs/BRIEF.md (API section)."""
    start, end = clip.bounds
    best = clip.best
    text = clip.text
    matched = [t for t in requested_tools if t in clip.tools]
    return {
        "clip_id": clip.clip_id,
        "talk_id": clip.first.talk_id,
        "title": clip.first.title,
        "speakers": list(clip.first.speakers),
        "start_sec": start,
        "end_sec": end,
        "watch_url": watch_url(clip.first.youtube_id, start),
        "quote": choose_quote(best.text, best.highlight),
        "why": template_why(text, matched, names, terms, pain),
        "matched_tools": matched,
        "matched_by": clip.matched_by,
        "talk_label": clip.first.talk_label,
        "score": round(clip.score, 6),
    }
