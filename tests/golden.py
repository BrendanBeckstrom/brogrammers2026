"""Golden stacks: the structural checks from docs/BRIEF.md "Acceptance".

    python -m tests.golden [--base-url http://localhost:8000]   (or `make golden`)

Sends every stack in tests/golden_stacks.yaml to POST /api/playlist (rerank off) and
prints pass/fail per check per stack. Exits 1 if any check fails. `check_playlist` is
also used by tests/test_search.py against a fake-backed API, so CI runs the same checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

STACKS_PATH = Path(__file__).resolve().parent / "golden_stacks.yaml"
DEFAULT_BASE_URL = "http://localhost:8000"
MIN_CLIPS, MAX_CLIPS = 6, 15
MIN_TALKS, MAX_PER_TALK = 4, 3
MIN_TOTAL_SEC, MAX_TOTAL_SEC = 900, 1800
MIN_CLIP_SEC, MAX_CLIP_SEC = 60, 240
MIN_MATCHED_SHARE = 0.8


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def load_stacks(path: Path = STACKS_PATH) -> list[dict[str, Any]]:
    stacks = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(stacks, list) or not all(isinstance(s, dict) for s in stacks):
        raise ValueError(f"{path} must be a YAML list of stacks")
    return stacks


def playlist_request(stack: dict[str, Any]) -> dict[str, Any]:
    """The POST /api/playlist body for a golden stack (rerank off: task 6 checks)."""
    return {
        "tools": list(stack.get("tools") or []),
        "pain": stack.get("pain"),
        "attended_talk_ids": list(stack.get("attended_talk_ids") or []),
        "filter": stack.get("filter", "all"),
        "budget_minutes": stack.get("budget_minutes", 25),
        "rerank": False,
    }


def check_playlist(stack: dict[str, Any], response: dict[str, Any]) -> list[Check]:
    """Every structural check from the brief that applies to this stack."""
    clips: list[dict[str, Any]] = response.get("clips") or []
    if stack.get("expect_empty"):
        return [
            Check("zero clips", not clips, f"{len(clips)} clips"),
            Check("has a message", bool(response.get("message")), repr(response.get("message"))),
        ]

    per_talk = Counter(c["talk_id"] for c in clips)
    total = response.get("total_seconds", 0)
    bad_clips: list[str] = []
    for c in clips:
        start, end = c["start_sec"], c["end_sec"]
        problems = []
        if not start < end:
            problems.append("start >= end")
        if not MIN_CLIP_SEC <= end - start <= MAX_CLIP_SEC:
            problems.append(f"length {end - start}s")
        if not str(c.get("watch_url", "")).endswith(f"&t={start}s"):
            problems.append("watch_url start")
        if problems:
            bad_clips.append(f"{c.get('clip_id')}: {', '.join(problems)}")
    matched = sum(1 for c in clips if c.get("matched_tools"))
    share = matched / len(clips) if clips else 0.0
    lengths = sum(c["end_sec"] - c["start_sec"] for c in clips)

    checks = [
        Check(
            f"{MIN_CLIPS}-{MAX_CLIPS} clips", MIN_CLIPS <= len(clips) <= MAX_CLIPS, str(len(clips))
        ),
        Check(f">= {MIN_TALKS} talks", len(per_talk) >= MIN_TALKS, str(len(per_talk))),
        Check(
            f"<= {MAX_PER_TALK} clips per talk",
            max(per_talk.values(), default=0) <= MAX_PER_TALK,
            f"max {max(per_talk.values(), default=0)}",
        ),
        Check(
            f"total_seconds {MIN_TOTAL_SEC}-{MAX_TOTAL_SEC}",
            MIN_TOTAL_SEC <= total <= MAX_TOTAL_SEC,
            str(total),
        ),
        Check("total_seconds = sum of clips", total == lengths, f"{total} vs {lengths}"),
        Check(
            f"every clip {MIN_CLIP_SEC}-{MAX_CLIP_SEC}s, start < end, watch_url &t=start",
            not bad_clips,
            "; ".join(bad_clips[:3]) or f"{len(clips)} ok",
        ),
        Check(
            f">= {MIN_MATCHED_SHARE:.0%} with matched_tools",
            share >= MIN_MATCHED_SHARE,
            f"{matched}/{len(clips)}",
        ),
    ]
    attended = set(stack.get("attended_talk_ids") or [])
    if stack.get("filter") == "missed":
        leaked = sorted({c["talk_id"] for c in clips} & attended)
        checks.append(Check("missed filter honoured", not leaked, ",".join(leaked) or "none"))
    if stack.get("filter") == "attended":
        outside = sorted({c["talk_id"] for c in clips} - attended)
        checks.append(Check("attended filter honoured", not outside, ",".join(outside) or "none"))
    return checks


Fetch = Callable[[dict[str, Any]], dict[str, Any]]


def http_fetch(base_url: str) -> Fetch:
    def fetch(body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            base_url.rstrip("/") + "/api/playlist",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    return fetch


def run(stacks: Sequence[dict[str, Any]], fetch: Fetch, out: Callable[[str], None] = print) -> int:
    """Run every stack; print per-check results. Returns the number of failed checks."""
    failed = 0
    for stack in stacks:
        name = stack.get("name", "?")
        if stack.get("skip"):
            out(f"SKIP {name}: {stack['skip']}")
            continue
        started = time.perf_counter()
        try:
            response = fetch(playlist_request(stack))
        except (urllib.error.URLError, OSError, ValueError) as e:
            out(f"FAIL {name}: request failed ({e})")
            failed += 1
            continue
        elapsed = time.perf_counter() - started
        count = len(response.get("clips") or [])
        out(f"{name}: {count} clips, {response.get('total_seconds')}s, {elapsed:.2f}s")
        for check in check_playlist(stack, response):
            failed += not check.ok
            out(f"  {'PASS' if check.ok else 'FAIL'} {check.name} ({check.detail})")
    out("all checks passed" if not failed else f"{failed} checks failed")
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the golden stacks against the API.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DEBRIEF_API_URL") or DEFAULT_BASE_URL,
        help="API base URL (env DEBRIEF_API_URL, default %(default)s)",
    )
    parser.add_argument("--stacks", type=Path, default=STACKS_PATH)
    args = parser.parse_args(argv)
    return 1 if run(load_stacks(args.stacks), http_fetch(args.base_url)) else 0


if __name__ == "__main__":
    sys.exit(main())
