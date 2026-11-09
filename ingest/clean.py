"""Turn a YouTube auto-caption WebVTT file into timed sentences.

YouTube's rolling captions repeat the previous line at the top of each cue and
mark every newly spoken word with an inline timestamp, e.g.
``staying<00:00:02.800><c> after</c>``. We keep each spoken word once, with its
start time, then group the words into sentences: on punctuation when there is
some, otherwise on pauses between words and a maximum length.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

PAUSE_SEC = 1.2  # a gap this long between two word starts ends a sentence
MAX_WORDS = 25  # captions are lightly punctuated, so cap sentence length
MAX_WORD_SEC = 2.0  # how long the last word of a sentence is assumed to last
MAX_ANNOTATION_WORDS = 4  # "[clears throat]" etc.; guards against a stray "["

_TS = r"(?:\d+:)?\d{1,2}:\d{2}\.\d{3}"
_CUE_TIMING = re.compile(rf"^\s*({_TS})\s+-->\s+({_TS})")
_INLINE_TS = re.compile(rf"<({_TS})>")
_TAG = re.compile(r"<[^>]*>")
_SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")
_SPEAKER_CHANGE = ">>"
_NON_CUE_BLOCK = re.compile(r"^\s*(WEBVTT|NOTE|STYLE|REGION)\b")


@dataclass(frozen=True)
class Cue:
    start_sec: float
    end_sec: float
    lines: tuple[str, ...]  # raw text lines, inline tags intact


@dataclass(frozen=True)
class Word:
    start_sec: float
    text: str


@dataclass(frozen=True)
class Sentence:
    start_sec: float
    end_sec: float
    text: str


def parse_timestamp(value: str) -> float:
    """Parse ``HH:MM:SS.mmm`` or ``MM:SS.mmm`` into seconds."""
    parts = value.split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2])
    hours = int(parts[-3]) if len(parts) > 2 else 0
    return hours * 3600 + minutes * 60 + seconds


def parse_cues(vtt: str) -> list[Cue]:
    """Split a WebVTT document into cues. Header, NOTE and STYLE blocks are skipped."""
    cues: list[Cue] = []
    # Cues end at an empty line. YouTube puts whitespace-only lines *inside* cues,
    # so those must not count as separators.
    blocks = re.split(r"\n{2,}", vtt.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = block.split("\n")
        for index, line in enumerate(lines):
            match = _CUE_TIMING.match(line)
            if match:
                text = tuple(t for t in lines[index + 1 :] if t.strip())
                start, end = (parse_timestamp(g) for g in match.groups())
                cues.append(Cue(start_sec=start, end_sec=end, lines=text))
                break
        else:
            # If those whitespace-only lines were stripped, a cue's text lands in its own
            # block right after the (now empty) cue: reattach it.
            if cues and not cues[-1].lines and not _NON_CUE_BLOCK.match(block):
                last = cues[-1]
                text = tuple(t for t in lines if t.strip())
                cues[-1] = Cue(start_sec=last.start_sec, end_sec=last.end_sec, lines=text)
    return cues


def plain_text(line: str) -> str:
    """A caption line without tags or entities, whitespace collapsed."""
    return " ".join(html.unescape(_TAG.sub(" ", line)).split())


def _line_words(line: str, cue_start: float) -> list[Word]:
    """Words of one caption line. Text before the first inline timestamp starts at the cue."""
    parts = _INLINE_TS.split(line)
    times = [cue_start, *(parse_timestamp(t) for t in parts[1::2])]
    words: list[Word] = []
    for start, segment in zip(times, parts[0::2], strict=True):
        for token in plain_text(segment).split():
            words.append(Word(start_sec=start, text=token))
    return words


def cue_words(cues: list[Cue]) -> list[Word]:
    """Every spoken word once, in order: drops the line each rolling cue repeats."""
    words: list[Word] = []
    last_line: str | None = None
    for cue in cues:
        lines = list(cue.lines)
        if lines and last_line is not None and plain_text(lines[0]) == last_line:
            lines = lines[1:]
        for line in lines:
            text = plain_text(line)
            if not text:
                continue
            words.extend(_line_words(line, cue.start_sec))
            last_line = text
    return words


def build_sentences(words: list[Word], talk_end: float | None = None) -> list[Sentence]:
    """Group words into sentences on punctuation, speaker changes, pauses and length.

    Speaker-change markers (``>>``) end a sentence and are dropped, as are sound
    annotations such as ``[applause]`` or ``[clears throat]``.
    """
    groups: list[list[Word]] = []
    current: list[Word] = []
    skip = 0  # words still to drop inside a "[...]" annotation

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for word in words:
        token = word.text
        if skip:
            skip = 0 if "]" in token else skip - 1
            continue
        if token.startswith(_SPEAKER_CHANGE):
            flush()
            token = token.lstrip(">")
            if not token:
                continue
            word = Word(start_sec=word.start_sec, text=token)
        if token.startswith("["):
            if "]" not in token:
                skip = MAX_ANNOTATION_WORDS - 1
            continue
        if current and (
            word.start_sec - current[-1].start_sec >= PAUSE_SEC or len(current) >= MAX_WORDS
        ):
            flush()
        current.append(word)
        if _SENTENCE_END.search(token):
            flush()
    flush()

    sentences: list[Sentence] = []
    for index, group in enumerate(groups):
        last_start = group[-1].start_sec
        if index + 1 < len(groups):
            limit = groups[index + 1][0].start_sec
        else:
            limit = talk_end if talk_end is not None else last_start + MAX_WORD_SEC
        end = max(last_start, min(limit, last_start + MAX_WORD_SEC))
        sentences.append(
            Sentence(
                start_sec=round(group[0].start_sec, 3),
                end_sec=round(end, 3),
                text=" ".join(w.text for w in group),
            )
        )
    return sentences


def clean_vtt(vtt: str) -> list[Sentence]:
    """Parse WebVTT text into de-duplicated, timed sentences."""
    cues = parse_cues(vtt)
    if not cues:
        return []
    return build_sentences(cue_words(cues), talk_end=max(c.end_sec for c in cues))


def load_sentences(path: Path) -> list[Sentence]:
    """Read a .vtt file and return its sentences."""
    return clean_vtt(path.read_text(encoding="utf-8"))
