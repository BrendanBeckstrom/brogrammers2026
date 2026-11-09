"""Cut cleaned caption sentences into overlapping ~60-90 second chunks.

Run ``python -m ingest.chunk`` to read ``data/talks.csv`` and
``data/raw/<youtube_id>.en.vtt`` and write ``data/chunks.jsonl``, one chunk
record per line (see the chunk record contract in docs/BRIEF.md).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingest.clean import Sentence, load_sentences

MIN_SEC = 60.0  # a window stops growing once it is at least this long ...
TARGET_SEC = 75.0  # ... and has reached the target length,
MAX_SEC = 90.0  # ... and never grows past this unless it is still under MIN_SEC
HARD_MAX_SEC = 98.0  # absolute cap (rounding to whole seconds adds up to 2 s)
OVERLAP_SEC = 15.0  # the next window starts at the first sentence this close to the end
TAIL_SEC = 30.0  # a leftover tail shorter than this is folded into the window before it
GAP_SEC = 30.0  # a caption gap longer than this (dropout, video, demo) starts a new segment

DEFAULT_TALKS = Path("data/talks.csv")
DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_OUT = Path("data/chunks.jsonl")


@dataclass(frozen=True)
class Chunk:
    start_sec: int
    end_sec: int
    text: str


@dataclass(frozen=True)
class Talk:
    youtube_id: str
    title: str
    speakers: list[str]
    track: str


def _windows(sentences: Sequence[Sentence]) -> list[tuple[int, int]]:
    """Inclusive (first, last) sentence index ranges for each chunk."""
    n = len(sentences)
    talk_end = sentences[-1].end_sec
    windows: list[tuple[int, int]] = []
    i = 0
    while True:
        start = sentences[i].start_sec
        j = i
        while j + 1 < n:
            length = sentences[j].end_sec - start
            grown = sentences[j + 1].end_sec - start
            if length >= TARGET_SEC or grown > HARD_MAX_SEC:
                break
            if grown > MAX_SEC and length >= MIN_SEC:
                break
            j += 1
        if (
            j + 1 < n
            and talk_end - sentences[j].end_sec < TAIL_SEC
            and talk_end - start <= HARD_MAX_SEC
        ):
            j = n - 1
        if j == n - 1:
            # last window: reach back so it is full length, but stay after the previous start
            first_allowed = windows[-1][0] + 1 if windows else 0
            while i > first_allowed and talk_end - sentences[i].start_sec < MIN_SEC:
                i -= 1
            windows.append((i, j))
            return windows
        windows.append((i, j))
        overlap_from = sentences[j].end_sec - OVERLAP_SEC
        k = i + 1
        while k <= j and sentences[k].start_sec < overlap_from:
            k += 1
        i = k


def _segments(sentences: Sequence[Sentence]) -> list[list[Sentence]]:
    """Split the talk wherever the captions go silent for more than GAP_SEC."""
    segments: list[list[Sentence]] = [[sentences[0]]]
    for previous, sentence in zip(sentences, sentences[1:], strict=False):
        if sentence.start_sec - previous.end_sec > GAP_SEC:
            segments.append([])
        segments[-1].append(sentence)
    return segments


def chunk_sentences(sentences: Sequence[Sentence]) -> list[Chunk]:
    """Group sentences into windows snapped to sentence boundaries, ~15 s overlap."""
    if not sentences:
        return []
    chunks: list[Chunk] = []
    for segment in _segments(sentences):
        for first, last in _windows(segment):
            part = segment[first : last + 1]
            chunks.append(
                Chunk(
                    start_sec=math.floor(part[0].start_sec),
                    end_sec=math.ceil(part[-1].end_sec),
                    text=" ".join(s.text for s in part),
                )
            )
    return chunks


def read_talks(path: Path) -> list[Talk]:
    """Rows of talks.csv (youtube_id,title,speakers,track); speakers are ';'-separated."""
    talks: list[Talk] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            youtube_id = (row.get("youtube_id") or "").strip()
            if not youtube_id or youtube_id in seen:
                continue
            seen.add(youtube_id)
            speakers = [s.strip() for s in (row.get("speakers") or "").split(";") if s.strip()]
            talks.append(
                Talk(
                    youtube_id=youtube_id,
                    title=(row.get("title") or "").strip(),
                    speakers=speakers,
                    track=(row.get("track") or "").strip(),
                )
            )
    return talks


def chunk_record(talk: Talk, chunk: Chunk) -> dict[str, Any]:
    """One line of chunks.jsonl, in the contract's field order."""
    return {
        "chunk_id": f"{talk.youtube_id}-{chunk.start_sec}",
        "talk_id": talk.youtube_id,
        "youtube_id": talk.youtube_id,
        "title": talk.title,
        "speakers": talk.speakers,
        "track": talk.track,
        "start_sec": chunk.start_sec,
        "end_sec": chunk.end_sec,
        "text": chunk.text,
        "tools": [],
        "talk_label": None,
    }


def caption_path(raw_dir: Path, youtube_id: str) -> Path:
    return raw_dir / f"{youtube_id}.en.vtt"


def run(talks_csv: Path, raw_dir: Path, out: Path) -> dict[str, Any]:
    """Chunk every talk with a caption file and write the JSONL. Returns a summary."""
    talks = read_talks(talks_csv)
    missing: list[str] = []
    empty: list[str] = []
    lengths: list[int] = []
    processed = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for talk in talks:
            path = caption_path(raw_dir, talk.youtube_id)
            if not path.is_file():
                missing.append(talk.youtube_id)
                continue
            chunks = chunk_sentences(load_sentences(path))
            if not chunks:
                empty.append(talk.youtube_id)
                continue
            processed += 1
            for chunk in chunks:
                handle.write(json.dumps(chunk_record(talk, chunk), ensure_ascii=False) + "\n")
                lengths.append(chunk.end_sec - chunk.start_sec)
    return {
        "talks_in_csv": len(talks),
        "talks_processed": processed,
        "chunks_written": len(lengths),
        "missing_captions": missing,
        "empty_captions": empty,
        "chunk_sec_min": min(lengths, default=0),
        "chunk_sec_median": statistics.median(lengths) if lengths else 0,
        "chunk_sec_max": max(lengths, default=0),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean captions and write chunks.jsonl")
    parser.add_argument("--talks", type=Path, default=DEFAULT_TALKS, help="talks.csv path")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="dir of .en.vtt")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="chunks.jsonl path")
    args = parser.parse_args(argv)
    if not args.talks.is_file():
        print(f"talks file not found: {args.talks}", file=sys.stderr)
        return 1
    summary = run(args.talks, args.raw_dir, args.out)
    print(f"talks in csv:     {summary['talks_in_csv']}")
    print(f"talks processed:  {summary['talks_processed']}")
    print(f"chunks written:   {summary['chunks_written']} -> {args.out}")
    print(
        "chunk seconds:    "
        f"min {summary['chunk_sec_min']}, median {summary['chunk_sec_median']}, "
        f"max {summary['chunk_sec_max']}"
    )
    missing = summary["missing_captions"]
    print(f"missing captions: {len(missing)}" + (f" ({', '.join(missing)})" if missing else ""))
    if summary["empty_captions"]:
        print(f"empty captions:   {', '.join(summary['empty_captions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
