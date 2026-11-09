"""Build the offline demo cache (task 13) in api/cache/ from a running API.

    DEBRIEF_API=https://brogrammers.cnh.radarhq.io python -m tests.build_cache   (or `make cache`)

Sends, and stores one JSON file per answer (see api/demo_cache.py for the key):

- `GET /api/tools` and `GET /api/talks`
- the demo go.mod (tests/fixtures/go.mod) through `POST /api/stack`, as the web app sends it
- every stack in tests/golden_stacks.yaml (a `dependency_file` stack is resolved through
  /api/stack first), plus the demo go.mod stack, to `POST /api/playlist` with rerank off and on
- `POST /api/memo` for each playlist's clips, as the web app's Memo button sends them
- `GET /api/clips/{id}` for every returned clip

Clip files hold a clip's full cleaned transcript, so they are gitignored (`clips-*.json`);
everything else holds ids, short quotes and model-written memo lines. Every file is scanned
for key-shaped secrets before the run counts as good; a file that matches is deleted. After
a clean run, cache files this run did not write are pruned.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from api import demo_cache
from api.routes_memo import MemoBody
from api.routes_search import PlaylistBody
from api.routes_stack import StackRequest
from tests import golden

DEFAULT_API = "http://localhost:8000"
DEMO_GO_MOD = Path(__file__).resolve().parent / "fixtures" / "go.mod"
REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SEC = 90
WORKERS = 4
# Key-shaped secrets only: a bare "sk-" also matches "task-" or "risk-" in talk text.
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"\bApiKey\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
)

Out = Callable[[str], None]


class Api(Protocol):
    def get(self, path: str) -> Any: ...

    def post(self, path: str, body: Mapping[str, Any]) -> Any: ...


class ApiError(RuntimeError):
    pass


class HttpApi:
    """The deployed (or local) API over urllib, one retry per request."""

    def __init__(self, base_url: str, retries: int = 1) -> None:
        self.base_url = base_url.rstrip("/")
        self.retries = retries

    def _send(self, request: urllib.request.Request) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as e:
                last = ApiError(f"{request.full_url} -> HTTP {e.code}")
                if e.code < 500:
                    break
            except (urllib.error.URLError, OSError, ValueError) as e:
                last = ApiError(f"{request.full_url} -> {type(e).__name__}")
            if attempt < self.retries:
                time.sleep(2)
        raise last or ApiError(request.full_url)

    def get(self, path: str) -> Any:
        return self._send(urllib.request.Request(self.base_url + path))

    def post(self, path: str, body: Mapping[str, Any]) -> Any:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(request)


@dataclass
class Summary:
    written: dict[Path, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.failures and not self.secrets


def find_secrets(text: str) -> list[str]:
    """Key-shaped matches in a cache file's text (pattern names only, never the match)."""
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def playlist_body(raw: Mapping[str, Any]) -> dict[str, Any]:
    return PlaylistBody.model_validate(raw).model_dump(mode="json")


def stack_body(name: str, content: str) -> dict[str, Any]:
    raw = {"tools": [], "dependency_file": {"name": name, "content": content}}
    return StackRequest.model_validate(raw).model_dump(mode="json")


def memo_body(tools: Sequence[str], clip_ids: Sequence[str]) -> dict[str, Any]:
    return MemoBody.model_validate({"tools": list(tools), "clip_ids": list(clip_ids)}).model_dump(
        mode="json"
    )


def _file_content(path: str) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate.read_text(encoding="utf-8")


def build(
    api: Api,
    root: Path,
    stacks: Sequence[Mapping[str, Any]],
    go_mod: str,
    out: Out = print,
    workers: int = WORKERS,
) -> Summary:
    """Send every demo request to `api` and store the answers under `root`."""
    summary = Summary()

    def save(route: str, body: Mapping[str, Any] | None, response: Any) -> None:
        path = demo_cache.store(route, body, response, root)
        summary.written[path] = route

    for route in ("tools", "talks"):
        rows = api.get(f"/api/{route}")
        save(route, None, rows)
        out(f"{route}: {len(rows)} rows")

    stacks_seen: dict[str, list[str]] = {}

    def resolve_stack(name: str, content: str) -> list[str]:
        body = stack_body(name, content)
        key = demo_cache.cache_key("stack", body)
        if key not in stacks_seen:
            response = api.post("/api/stack", body)
            save("stack", body, response)
            stacks_seen[key] = [t["id"] for t in response["tools"]]
            unknown = len(response.get("unknown_packages") or [])
            out(f"stack {name}: {stacks_seen[key]} ({unknown} unknown packages)")
        return stacks_seen[key]

    # The playlists to cache: every golden stack, plus the demo go.mod as the web app sends it.
    plans: list[tuple[str, dict[str, Any]]] = []
    for stack in stacks:
        stack = dict(stack)
        dep = stack.get("dependency_file")
        if dep:
            stack["tools"] = resolve_stack(Path(str(dep)).name, _file_content(str(dep)))
        plans.append((str(stack.get("name", "?")), stack))
    demo_tools = resolve_stack("go.mod", go_mod)
    plans.append(("demo go.mod", {"tools": demo_tools, "pain": ""}))

    playlists: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for name, stack in plans:
        for rerank in (False, True):
            body = playlist_body(golden.playlist_request(stack, rerank))
            key = demo_cache.cache_key("playlist", body)
            if key in playlists:
                continue
            label = f"{name} rerank={'on' if rerank else 'off'}"
            started = time.perf_counter()
            try:
                response = api.post("/api/playlist", body)
            except ApiError as e:
                summary.failures.append(f"playlist {label}: {e}")
                out(f"FAIL playlist {label}: {e}")
                continue
            save("playlist", body, response)
            playlists[key] = (label, body, response)
            status = (response.get("debug") or {}).get("rerank")
            out(
                f"playlist {label}: {len(response.get('clips') or [])} clips, "
                f"{response.get('total_seconds')}s, {time.perf_counter() - started:.1f}s"
                + (f", rerank {status}" if rerank and status else "")
            )

    memos: dict[str, tuple[str, dict[str, Any]]] = {}
    clip_ids: dict[str, None] = {}
    for label, body, response in playlists.values():
        ids = [c["clip_id"] for c in response.get("clips") or []]
        clip_ids.update(dict.fromkeys(ids))
        if ids:
            memo = memo_body(body["tools"], ids)
            memos.setdefault(demo_cache.cache_key("memo", memo), (label, memo))

    def fetch_memo(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any], Any]:
        label, body = item
        try:
            return label, body, api.post("/api/memo", body)
        except ApiError as e:
            return label, body, e

    def fetch_clip(clip_id: str) -> tuple[str, Any]:
        try:
            return clip_id, api.get("/api/clips/" + urllib.parse.quote(clip_id, safe=""))
        except ApiError as e:
            return clip_id, e

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for label, body, result in pool.map(fetch_memo, memos.values()):
            if isinstance(result, Exception):
                summary.failures.append(f"memo {label}: {result}")
                out(f"FAIL memo {label}: {result}")
                continue
            save("memo", body, result)
            out(f"memo {label}: cites {len(result.get('cited_clip_ids') or [])} clips")
        clip_failures = 0
        for clip_id, result in pool.map(fetch_clip, clip_ids):
            if isinstance(result, Exception):
                summary.failures.append(f"clip {clip_id}: {result}")
                clip_failures += 1
                continue
            save("clips", {"clip_id": clip_id}, result)
        out(f"clips: {len(clip_ids) - clip_failures}/{len(clip_ids)} stored")

    for path in list(summary.written):
        hits = find_secrets(path.read_text(encoding="utf-8"))
        if hits:
            path.unlink()
            del summary.written[path]
            summary.secrets.append(f"{path.name}: {', '.join(hits)}")
            out(f"DELETED {path.name}: looks like it holds a secret ({', '.join(hits)})")
    return summary


def prune(root: Path, keep: Iterable[Path]) -> list[Path]:
    """Remove cache files a clean run did not write (stacks that changed or went away)."""
    keep_set = {p.resolve() for p in keep}
    stale = [p for p in root.glob("*.json") if p.resolve() not in keep_set]
    for path in stale:
        path.unlink()
    return stale


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline demo cache in api/cache/.")
    parser.add_argument(
        "--api",
        default=os.environ.get("DEBRIEF_API") or os.environ.get("DEBRIEF_API_URL") or DEFAULT_API,
        help="API base URL (env DEBRIEF_API, default %(default)s)",
    )
    parser.add_argument("--out", type=Path, default=demo_cache.cache_dir())
    parser.add_argument("--stacks", type=Path, default=golden.STACKS_PATH)
    parser.add_argument("--go-mod", type=Path, default=DEMO_GO_MOD)
    args = parser.parse_args(argv)

    print(f"building the demo cache from {args.api} into {args.out}")
    summary = build(
        HttpApi(args.api),
        args.out,
        golden.load_stacks(args.stacks),
        args.go_mod.read_text(encoding="utf-8"),
    )
    if summary.ok():
        for path in prune(args.out, summary.written):
            print(f"pruned stale {path.name}")
    size = sum(p.stat().st_size for p in summary.written)
    counts: dict[str, int] = {}
    for route in summary.written.values():
        counts[route] = counts.get(route, 0) + 1
    print(f"wrote {len(summary.written)} files, {size / 1024:.0f} KiB: {counts}")
    for problem in summary.failures + summary.secrets:
        print(f"problem: {problem}")
    return 0 if summary.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
