"""Tag chunks with the tools they mention, using the dictionary in data/tools.yaml (task 4).

`tag_text` is pure: it returns the ids of tools whose aliases or caption spellings appear
in a text as whole words, case-insensitive. Words inside a term may be separated by
spaces, hyphens or nothing, so "argo cd" matches "Argo CD", "argo-cd" and "ArgoCD".
Tools marked `exclude_from_matching` never match.

CLI (`make ingest` runs it after chunking):

    python -m ingest.tag [--chunks data/chunks.jsonl] [--tools data/tools.yaml]

It sets `tools` (sorted, unique) on every chunk, rewrites the file in place, and prints
chunks per tool plus how many tools have at least 5 chunks. Paths can also come from
the DEBRIEF_CHUNKS and DEBRIEF_TOOLS env vars; args win over env.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CHUNKS = Path("data/chunks.jsonl")
DEFAULT_TOOLS = Path("data/tools.yaml")
MIN_CHUNKS = 5  # a tool is useful to the API only with at least this many chunks

PACKAGE_ECOSYSTEMS = ("go", "npm", "pypi", "helm")
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WORD_SPLIT = re.compile(r"[\s\-]+")
_WORD_JOIN = r"[\s\-]*"  # between words of a term: spaces, hyphens or nothing


@dataclass(frozen=True)
class Tool:
    """One entry of data/tools.yaml."""

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    caption_spellings: tuple[str, ...] = ()
    packages: dict[str, tuple[str, ...]] = field(default_factory=dict, compare=False)
    exclude_from_matching: bool = False

    @property
    def terms(self) -> tuple[str, ...]:
        """Every form that counts as a mention: aliases first, then caption spellings."""
        return self.aliases + self.caption_spellings


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where} must be a list of strings")
    return tuple(v.strip() for v in value if v.strip())


def _parse_tool(entry: Any, index: int) -> Tool:
    if not isinstance(entry, dict):
        raise ValueError(f"tools.yaml entry {index} must be a mapping")
    tool_id = entry.get("id")
    if not isinstance(tool_id, str) or not _ID_RE.match(tool_id):
        raise ValueError(f"tools.yaml entry {index}: id must be lowercase kebab-case")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"tool {tool_id}: name is required")
    raw_packages = entry.get("packages") or {}
    if not isinstance(raw_packages, dict):
        raise ValueError(f"tool {tool_id}: packages must be a mapping")
    packages = {
        eco: _str_list(raw_packages.get(eco), f"tool {tool_id}: packages.{eco}")
        for eco in PACKAGE_ECOSYSTEMS
    }
    return Tool(
        id=tool_id,
        name=name.strip(),
        aliases=_str_list(entry.get("aliases"), f"tool {tool_id}: aliases"),
        caption_spellings=_str_list(
            entry.get("caption_spellings"), f"tool {tool_id}: caption_spellings"
        ),
        packages=packages,
        exclude_from_matching=bool(entry.get("exclude_from_matching", False)),
    )


def load_tools(path: str | Path = DEFAULT_TOOLS) -> list[Tool]:
    """Load and validate the tool dictionary. Raises ValueError on a malformed file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a YAML list of tools")
    tools = [_parse_tool(entry, i) for i, entry in enumerate(data)]
    seen: set[str] = set()
    for tool in tools:
        if tool.id in seen:
            raise ValueError(f"duplicate tool id: {tool.id}")
        seen.add(tool.id)
    return tools


def _term_regex(term: str) -> str:
    words = [w for w in _WORD_SPLIT.split(term.strip().lower()) if w]
    return _WORD_JOIN.join(re.escape(w) for w in words)


@lru_cache(maxsize=1024)
def _compile(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    alternatives = sorted({r for r in map(_term_regex, terms) if r}, key=len, reverse=True)
    if not alternatives:
        return None
    # (?<!\w) / (?!\w): whole words only; hyphens and apostrophes count as boundaries,
    # so "cert-manager-webhook" and "Istio's" still mention their tool.
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)


def tag_text(text: str, tools: Iterable[Tool]) -> list[str]:
    """Return the sorted, unique ids of tools mentioned in `text`."""
    if not text:
        return []
    found: set[str] = set()
    for tool in tools:
        if tool.exclude_from_matching:
            continue
        pattern = _compile(tool.terms)
        if pattern is not None and pattern.search(text):
            found.add(tool.id)
    return sorted(found)


def tag_chunks(chunks: Iterable[dict[str, Any]], tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Set `tools` on every chunk from its `text`. Returns new dicts; inputs are untouched."""
    return [{**chunk, "tools": tag_text(str(chunk.get("text") or ""), tools)} for chunk in chunks]


def chunks_per_tool(
    chunks: Iterable[dict[str, Any]], tools: Sequence[Tool]
) -> list[tuple[str, int]]:
    """Chunk count for every matchable tool, most chunks first (ties by id)."""
    counts: Counter[str] = Counter()
    for chunk in chunks:
        counts.update(set(chunk.get("tools") or []))
    ids = [t.id for t in tools if not t.exclude_from_matching]
    return sorted(((i, counts[i]) for i in ids), key=lambda pair: (-pair[1], pair[0]))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Write records atomically: a crash mid-write never leaves a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def format_report(counts: Sequence[tuple[str, int]], total_chunks: int) -> str:
    width = max((len(tool_id) for tool_id, _ in counts), default=4)
    lines = [f"{'tool':<{width}}  chunks"]
    lines += [f"{tool_id:<{width}}  {n:>6}" for tool_id, n in counts]
    useful = sum(1 for _, n in counts if n >= MIN_CHUNKS)
    tagged = f"{total_chunks} chunks tagged"
    lines.append(f"{tagged}; {useful} of {len(counts)} tools have >= {MIN_CHUNKS} chunks")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tag chunks with tool ids from tools.yaml.")
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path(os.environ.get("DEBRIEF_CHUNKS") or DEFAULT_CHUNKS),
        help="chunks JSONL to tag in place (env DEBRIEF_CHUNKS, default %(default)s)",
    )
    parser.add_argument(
        "--tools",
        type=Path,
        default=Path(os.environ.get("DEBRIEF_TOOLS") or DEFAULT_TOOLS),
        help="tool dictionary (env DEBRIEF_TOOLS, default %(default)s)",
    )
    args = parser.parse_args(argv)

    if not args.chunks.exists():
        print(f"error: {args.chunks} not found; run the chunk step first", file=sys.stderr)
        return 1
    tools = load_tools(args.tools)
    chunks = tag_chunks(read_jsonl(args.chunks), tools)
    write_jsonl(args.chunks, chunks)
    print(format_report(chunks_per_tool(chunks, tools), len(chunks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
