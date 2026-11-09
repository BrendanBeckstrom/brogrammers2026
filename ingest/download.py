"""Fetch English automatic captions for every talk in data/talks.csv.

Writes data/raw/<youtube_id>.en.vtt per row. Captions only, never video. Rows whose
caption file already exists are skipped, so a rerun only fetches what is missing.
Prints a summary and every talk it could not fetch; exits 1 if any failed.

Run: uv run python -m ingest.download [--talks data/talks.csv] [--raw-dir data/raw]
"""

import argparse
import csv
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp
from opentelemetry import trace

from api import telemetry

DEFAULT_TALKS = Path("data/talks.csv")
DEFAULT_RAW_DIR = Path("data/raw")

# Spacing that kept YouTube from answering HTTP 429 (1 s spacing did not).
POLITE_SLEEPS: dict[str, int] = {
    "sleep_interval_requests": 3,
    "sleep_interval_subtitles": 6,
    "sleep_interval": 4,
    "max_sleep_interval": 8,
}

# Takes a YouTube id and the output directory; writes <id>.en.vtt there or raises.
Fetcher = Callable[[str, Path], None]


@dataclass(frozen=True)
class Talk:
    youtube_id: str
    title: str
    speakers: tuple[str, ...]
    track: str


@dataclass
class DownloadReport:
    present: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (youtube_id, reason)


def read_talks(path: Path) -> list[Talk]:
    """Parse talks.csv (header youtube_id,title,speakers,track; speakers split on ';')."""
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    talks: list[Talk] = []
    for row in rows:
        youtube_id = (row.get("youtube_id") or "").strip()
        if not youtube_id:
            continue
        speakers = tuple(s.strip() for s in (row.get("speakers") or "").split(";") if s.strip())
        talks.append(
            Talk(
                youtube_id=youtube_id,
                title=(row.get("title") or "").strip(),
                speakers=speakers,
                track=(row.get("track") or "").strip(),
            )
        )
    return talks


def caption_path(raw_dir: Path, youtube_id: str) -> Path:
    return raw_dir / f"{youtube_id}.en.vtt"


def ydl_options(raw_dir: Path) -> dict[str, Any]:
    return {
        "skip_download": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "vtt",
        "outtmpl": {"default": str(raw_dir / "%(id)s.%(ext)s")},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        **POLITE_SLEEPS,
    }


log = logging.getLogger("debrief.ingest")
tracer = trace.get_tracer("debrief")


def _failed(report: DownloadReport, youtube_id: str, reason: str) -> None:
    report.failed.append((youtube_id, reason))
    log.warning(
        "caption fetch failed",
        extra={"debrief.youtube_id": youtube_id, "debrief.reason": reason[:200]},
    )


def fetch_captions(youtube_id: str, raw_dir: Path) -> None:
    """Ask yt-dlp for one video's English auto-captions. The only network call here."""
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    with yt_dlp.YoutubeDL(ydl_options(raw_dir)) as ydl:
        ydl.download([url])


def _reason(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text.removeprefix("ERROR: ")


def download_all(
    talks: Sequence[Talk], raw_dir: Path, fetch: Fetcher | None = None
) -> DownloadReport:
    """Fetch every missing caption file; never stops on a single failure."""
    fetch = fetch or fetch_captions
    raw_dir.mkdir(parents=True, exist_ok=True)
    report = DownloadReport()
    total = len(talks)
    for n, talk in enumerate(talks, start=1):
        target = caption_path(raw_dir, talk.youtube_id)
        if target.exists():
            report.present.append(talk.youtube_id)
            continue
        print(f"[{n}/{total}] fetching {talk.youtube_id}", flush=True)
        try:
            fetch(talk.youtube_id, raw_dir)
        except Exception as exc:  # one bad talk must not stop the batch
            _failed(report, talk.youtube_id, _reason(exc))
            continue
        if target.exists():
            report.fetched.append(talk.youtube_id)
        else:
            _failed(report, talk.youtube_id, "no English automatic captions")
    return report


def print_report(talks: Sequence[Talk], report: DownloadReport) -> None:
    titles = {t.youtube_id: t.title for t in talks}
    ok = len(report.present) + len(report.fetched)
    print(
        f"{ok}/{len(talks)} caption files present "
        f"({len(report.present)} already there, {len(report.fetched)} fetched), "
        f"{len(report.failed)} failed"
    )
    if report.failed:
        print("Could not fetch:")
        for youtube_id, reason in report.failed:
            print(f"  {youtube_id}  {titles.get(youtube_id, '')}  -- {reason}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--talks", type=Path, default=DEFAULT_TALKS, help="talks.csv path")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="caption dir")
    args = parser.parse_args(argv)

    telemetry.setup(telemetry.INGEST_SERVICE)
    try:
        with tracer.start_as_current_span("ingest.download") as span:
            talks = read_talks(args.talks)
            report = download_all(talks, args.raw_dir)
            span.set_attributes(
                {
                    "debrief.talks": len(talks),
                    "debrief.present": len(report.present),
                    "debrief.fetched": len(report.fetched),
                    "debrief.failed": len(report.failed),
                }
            )
            print_report(talks, report)
            return 1 if report.failed else 0
    finally:
        telemetry.flush()


if __name__ == "__main__":
    sys.exit(main())
