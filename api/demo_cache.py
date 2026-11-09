"""Demo cache (task 13): with DEBRIEF_CACHE=on, known requests are answered from api/cache/.

Demo safety on bad venue Wi-Fi. `python -m tests.build_cache` (or `make cache`) sends the golden
stacks, their memos, the demo go.mod, /api/tools, /api/talks and every returned clip to a
running API and writes one JSON file per answer here. With the flag on, the routes look the
request up first: a hit is returned without touching Elastic or the model, marked with the
header `X-Debrief-Cache: hit` (and `debug.cache` when the answer has a `debug` object); a miss
falls through to the live path, and if that is unreachable the route answers 503 with a clear
message instead of crashing. With the flag off nothing here runs.

A file is keyed by the route plus a SHA-256 of the normalised request body: sorted keys,
sorted and de-duplicated id lists (`tools`, `attended_talk_ids`, `clip_ids`), and an empty
pain sentence treated as none. So `{"tools": ["b", "a"]}` and `{"tools": ["a", "b"]}` share an
answer. Callers pass the body after pydantic validation, so defaults are filled in too.

`DEBRIEF_CACHE_DIR` overrides the directory (tests use a temporary one).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Response
from opentelemetry import trace

from api.search import SearchBackendError

CACHE_DIR = Path(__file__).resolve().parent / "cache"
HEADER = "X-Debrief-Cache"
HIT, MISS = "hit", "miss"
SPAN_ATTRIBUTE = "cache.hit"
# Request fields that are sets of ids: order and repeats never change the answer.
ID_LISTS = ("tools", "attended_talk_ids", "clip_ids")
ROUTES = ("playlist", "memo", "stack", "tools", "talks", "clips")

log = logging.getLogger("debrief.cache")


def enabled() -> bool:
    """True when DEBRIEF_CACHE=on (also accepts 1/true/yes)."""
    return os.environ.get("DEBRIEF_CACHE", "").strip().lower() in {"on", "1", "true", "yes"}


def cache_dir() -> Path:
    override = os.environ.get("DEBRIEF_CACHE_DIR", "").strip()
    return Path(override) if override else CACHE_DIR


def normalise(body: Mapping[str, Any] | None) -> dict[str, Any]:
    """The body in canonical form: id lists sorted and de-duplicated, blank pain dropped."""
    out: dict[str, Any] = dict(body or {})
    for key in ID_LISTS:
        value = out.get(key)
        if isinstance(value, list):
            out[key] = sorted({str(v).strip() for v in value if str(v).strip()})
    if "pain" in out:
        pain = out["pain"]
        out["pain"] = (pain.strip() or None) if isinstance(pain, str) else pain
    return out


def cache_key(route: str, body: Mapping[str, Any] | None = None) -> str:
    """A stable SHA-256 hex digest of the route and the normalised body (sorted keys)."""
    canonical = json.dumps(
        {"route": route, "body": normalise(body)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_path(route: str, body: Mapping[str, Any] | None = None, root: Path | None = None) -> Path:
    if route not in ROUTES:
        raise ValueError(f"unknown cache route {route!r}")
    return (root or cache_dir()) / f"{route}-{cache_key(route, body)[:20]}.json"


def store(
    route: str, body: Mapping[str, Any] | None, response: Any, root: Path | None = None
) -> Path:
    """Write one cached answer; the file keeps the normalised request so a person can read it."""
    path = cache_path(route, body, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"route": route, "request": normalise(body), "response": response}
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def lookup(route: str, body: Mapping[str, Any] | None = None, root: Path | None = None) -> Any:
    """The cached response for this request, or None. A broken file counts as a miss."""
    path = cache_path(route, body, root)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning(
            "unreadable cache file",
            extra={"debrief.cache.file": path.name, "error.type": type(e).__name__},
        )
        return None
    if not isinstance(record, dict) or record.get("request") != normalise(body):
        return None  # a different request with the same key prefix, or a hand-edited file
    return record.get("response")


def serve(
    route: str,
    body: Mapping[str, Any] | None,
    span: trace.Span,
    response: Response,
) -> Any:
    """For a route: the cached answer when the cache is on and has one, else None.

    Sets the `cache.hit` span attribute and the X-Debrief-Cache header (only when the cache is
    on, so the flag-off path is unchanged), and adds `debug.cache: "hit"` to answers that carry
    a `debug` object.
    """
    if not enabled():
        return None
    cached = lookup(route, body)
    hit = cached is not None
    span.set_attribute(SPAN_ATTRIBUTE, hit)
    response.headers[HEADER] = HIT if hit else MISS
    if hit and isinstance(cached, dict) and isinstance(cached.get("debug"), dict):
        cached["debug"]["cache"] = HIT
    return cached


def miss_error(reason: str) -> HTTPException:
    """The 503 a cache miss gets when the live path is unreachable (cache on, network off)."""
    return HTTPException(
        status_code=503,
        detail=(
            "This request is not in the offline demo cache (DEBRIEF_CACHE=on) and the live "
            f"service is unreachable: {reason}"
        ),
        headers={HEADER: MISS},
    )


class OfflineSearchClient:
    """Stands in for Elastic when the cache is on but ELASTIC_URL/ELASTIC_API_KEY are unset.

    Cached requests never call it; a miss fails on first use, which the route turns into a 503.
    """

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        raise SearchBackendError("Elastic is not configured")

    def mget(self, index: str, ids: Sequence[str]) -> list[dict[str, Any]]:
        raise SearchBackendError("Elastic is not configured")
