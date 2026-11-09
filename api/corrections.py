"""Readable transcripts: misheard tool names in auto-captions put right (accessibility).

`correct(text)` replaces every caption spelling from data/tools.yaml ("psyllium", "hotel
collector") with the tool's name, as whole words, case-insensitive, words joined by spaces
or hyphens. A spelling often carries a context word so that it is not ordinary English
("hotel collector", not bare "hotel"); that word stays, so "hotel collector" becomes
"OpenTelemetry collector" rather than "OpenTelemetry". Which leading words of a spelling
stand for the tool is worked out once per spelling, by how closely they spell the tool's
name or one of its aliases (see `misheard_words`).

Pure functions over the tool catalog; no network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from ingest.tag import Tool

# A later prefix of the spelling wins while it scores within this of the best one, so
# "argo city" maps whole to "Argo CD" but "spiffy id" keeps its "id".
PREFIX_TOLERANCE = 0.1


def display_name(tool: Tool) -> str:
    """The tool's name for running text: "SPIFFE / SPIRE" reads as "SPIFFE"."""
    return tool.name.split(" / ")[0].strip() or tool.name


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def misheard_words(spelling: str, tool: Tool) -> int:
    """How many leading words of `spelling` are the misheard tool name; the rest is context.

    Scores each prefix by its best similarity to the tool's name or an alias (letters and
    digits only) and takes the longest prefix within PREFIX_TOLERANCE of the best score.
    """
    words = spelling.lower().split()
    forms = {_squash(f) for f in (display_name(tool), tool.name, *tool.aliases)} - {""}
    scores = [
        max(SequenceMatcher(None, "".join(words[:k]), form).ratio() for form in forms)
        for k in range(1, len(words) + 1)
    ]
    best = max(scores)
    return max(k for k, score in enumerate(scores, 1) if score >= best - PREFIX_TOLERANCE)


def replacement(spelling: str, tool: Tool) -> str:
    """What a caption spelling becomes: the tool's name plus any trailing context words."""
    words = spelling.split()
    return " ".join([display_name(tool), *words[misheard_words(spelling, tool) :]])


@lru_cache(maxsize=8)
def _compiled(
    tools: tuple[Tool, ...],
) -> tuple[re.Pattern[str] | None, dict[str, tuple[str, int]]]:
    """One regex for every spelling, and spelling -> (tool name, misheard word count)."""
    by_key: dict[str, tuple[str, int]] = {}
    for tool in tools:
        for spelling in tool.caption_spellings:
            key = " ".join(spelling.lower().split())
            by_key.setdefault(key, (display_name(tool), misheard_words(spelling, tool)))
    if not by_key:
        return None, by_key
    # Longest first, so "hotel collectors" wins over "hotel collector".
    alternatives = sorted(by_key, key=lambda k: (-len(k), k))
    body = "|".join(r"[\s\-]+".join(map(re.escape, k.split())) for k in alternatives)
    # Hyphens count as word boundaries, as in ingest/tag.py: "Celium-based" is corrected too.
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE), by_key


def correct(text: str, tools: Sequence[Tool]) -> tuple[str, list[dict[str, str]]]:
    """(`text` with caption spellings replaced, `[{from, to}]` once per distinct replacement).

    Replacements that differ only in case ("Psyllium", "psyllium") are listed once, as first
    seen.
    """
    pattern, by_key = _compiled(tuple(tools))
    if pattern is None or not text:
        return text, []
    seen: dict[str, dict[str, str]] = {}

    def swap(match: re.Match[str]) -> str:
        found = match.group(0)
        words = re.split(r"[\s\-]+", found)
        name, misheard = by_key[" ".join(words).lower()]
        to = " ".join([name, *words[misheard:]])  # context words keep their captioned case
        seen.setdefault(" ".join(words).lower(), {"from": found, "to": to})
        return to

    corrected = pattern.sub(swap, text)
    return corrected, list(seen.values())


def with_corrections(clip: Mapping[str, Any], tools: Sequence[Tool]) -> dict[str, Any]:
    """A clip answer with `text_corrected` and `corrections` added; `text` is unchanged."""
    text = str(clip.get("text") or "")
    corrected, corrections = correct(text, tools)
    return {**clip, "text_corrected": corrected, "corrections": corrections}
