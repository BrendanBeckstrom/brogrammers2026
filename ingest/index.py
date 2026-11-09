"""Task 5: create the `clips` and `talks` indexes and bulk load the chunk records.

    python -m ingest.index [--chunks data/chunks.jsonl] [--recreate] [--batch-size 50]

Config comes only from env: ELASTIC_URL and ELASTIC_API_KEY. Clips are indexed with
`_id` = chunk_id and talks with `_id` = talk_id, so a re-run overwrites instead of
duplicating. Progress lines carry ids and counts only, never transcript text.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from elasticsearch import BadRequestError, Elasticsearch

CLIPS_INDEX = "clips"
TALKS_INDEX = "talks"
DEFAULT_CHUNKS_PATH = Path("data/chunks.jsonl")
# semantic_text runs inference at index time, so keep batches small and the timeout long.
DEFAULT_BATCH_SIZE = 50
REQUEST_TIMEOUT_SEC = 300.0
MAX_ITEM_ATTEMPTS = 4
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Exactly the `clips` mapping from docs/BRIEF.md. No inference_id: the deployment's default
# inference endpoint is used.
CLIPS_MAPPING: dict[str, Any] = {
    "properties": {
        "chunk_id": {"type": "keyword"},
        "talk_id": {"type": "keyword"},
        "youtube_id": {"type": "keyword"},
        "title": {"type": "text"},
        "speakers": {"type": "keyword"},
        "track": {"type": "keyword"},
        "start_sec": {"type": "integer"},
        "end_sec": {"type": "integer"},
        "text": {"type": "text", "copy_to": "text_semantic"},
        "text_semantic": {"type": "semantic_text"},
        "tools": {"type": "keyword"},
        "talk_label": {"type": "keyword"},
    }
}

# Fallback the brief allows if Elastic refuses `copy_to` into `semantic_text`: no copy_to,
# and the loader writes the text into both fields instead.
CLIPS_MAPPING_NO_COPY_TO: dict[str, Any] = {
    "properties": {
        **CLIPS_MAPPING["properties"],
        "text": {"type": "text"},
    }
}

TALKS_MAPPING: dict[str, Any] = {
    "properties": {
        "talk_id": {"type": "keyword"},
        "youtube_id": {"type": "keyword"},
        "title": {"type": "text"},
        "speakers": {"type": "keyword"},
        "track": {"type": "keyword"},
        "talk_label": {"type": "keyword"},
    }
}

# Fields of the chunk record contract; anything else in a line is not indexed.
CHUNK_FIELDS = (
    "chunk_id",
    "talk_id",
    "youtube_id",
    "title",
    "speakers",
    "track",
    "start_sec",
    "end_sec",
    "text",
    "tools",
    "talk_label",
)
TALK_FIELDS = ("talk_id", "youtube_id", "title", "speakers", "track", "talk_label")

Doc = dict[str, Any]
Progress = Callable[[str, int, int], None]


def read_chunks(path: Path) -> list[Doc]:
    """Read chunk records from a JSONL file, skipping blank lines."""
    chunks: list[Doc] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: invalid JSON ({e.msg})") from e
            if (
                not isinstance(record, dict)
                or not record.get("chunk_id")
                or not record.get("talk_id")
            ):
                raise ValueError(f"{path}:{line_no}: record needs chunk_id and talk_id")
            chunks.append(record)
    return chunks


def clip_doc(chunk: Doc, write_semantic: bool) -> Doc:
    """The `clips` document for one chunk record."""
    doc = {field: chunk[field] for field in CHUNK_FIELDS if field in chunk}
    if write_semantic:
        doc["text_semantic"] = doc.get("text", "")
    return doc


def derive_talks(chunks: Iterable[Doc]) -> list[Doc]:
    """One `talks` document per talk_id, in first-seen order.

    Each field takes its first non-empty value across the talk's chunks.
    """
    talks: dict[str, Doc] = {}
    for chunk in chunks:
        talk = talks.setdefault(chunk["talk_id"], {"talk_id": chunk["talk_id"]})
        for field in TALK_FIELDS:
            if field in chunk and talk.get(field) in (None, "", []):
                talk[field] = chunk[field]
    return list(talks.values())


def _has_copy_to(client: Elasticsearch) -> bool:
    mapping = client.indices.get_mapping(index=CLIPS_INDEX)
    text = mapping[CLIPS_INDEX]["mappings"]["properties"].get("text", {})
    copy_to = text.get("copy_to", [])
    return "text_semantic" in ([copy_to] if isinstance(copy_to, str) else copy_to)


def ensure_indexes(client: Elasticsearch, recreate: bool) -> bool:
    """Create `clips` and `talks` if missing (dropping them first when `recreate`).

    Returns True when `clips` has no copy_to into `text_semantic`, meaning the loader
    must write the text into `text_semantic` itself.
    """
    if recreate:
        for index in (CLIPS_INDEX, TALKS_INDEX):
            client.indices.delete(index=index, ignore_unavailable=True)
            print(f"{index}: deleted", file=sys.stderr)

    write_semantic = False
    if client.indices.exists(index=CLIPS_INDEX):
        write_semantic = not _has_copy_to(client)
        print(f"{CLIPS_INDEX}: exists, keeping its mapping", file=sys.stderr)
    else:
        try:
            client.indices.create(index=CLIPS_INDEX, mappings=CLIPS_MAPPING)
            print(f"{CLIPS_INDEX}: created", file=sys.stderr)
        except BadRequestError as e:
            if "copy_to" not in str(e):
                raise
            client.indices.create(index=CLIPS_INDEX, mappings=CLIPS_MAPPING_NO_COPY_TO)
            write_semantic = True
            print(
                f"{CLIPS_INDEX}: created without copy_to (refused); writing text_semantic directly",
                file=sys.stderr,
            )

    if client.indices.exists(index=TALKS_INDEX):
        print(f"{TALKS_INDEX}: exists, keeping its mapping", file=sys.stderr)
    else:
        client.indices.create(index=TALKS_INDEX, mappings=TALKS_MAPPING)
        print(f"{TALKS_INDEX}: created", file=sys.stderr)
    return write_semantic


def _describe_error(doc_id: str, item: Doc) -> str:
    """Status, error type and reason for a failed item, without any field values."""
    error = item.get("error") or {}
    if not isinstance(error, dict):
        error = {"reason": str(error)}
    # Elastic can quote the offending value ("Preview of field's value: ..."); keep it out.
    reason = str(error.get("reason", "")).split("Preview of field", 1)[0].strip()
    return f"{doc_id}: status {item.get('status')} {error.get('type', '?')}: {reason[:200]}"


def bulk_index(
    client: Elasticsearch,
    index: str,
    docs: Sequence[tuple[str, Doc]],
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: Progress | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Index `(id, doc)` pairs in batches; returns one message per document that failed.

    Items rejected with a retryable status (e.g. 429 while inference is busy) are re-sent,
    with backoff, up to MAX_ITEM_ATTEMPTS times.
    """
    failures: list[str] = []
    done = 0
    for start in range(0, len(docs), batch_size):
        pending = list(docs[start : start + batch_size])
        for attempt in range(1, MAX_ITEM_ATTEMPTS + 1):
            operations: list[Doc] = []
            for doc_id, doc in pending:
                operations.append({"index": {"_index": index, "_id": doc_id}})
                operations.append(doc)
            response = client.bulk(operations=operations)
            if not response["errors"]:
                break
            retry: list[tuple[str, Doc]] = []
            for (doc_id, doc), entry in zip(pending, response["items"], strict=True):
                item = next(iter(entry.values()))
                status = int(item.get("status", 0))
                if status < 300:
                    continue
                if status in RETRYABLE_STATUSES and attempt < MAX_ITEM_ATTEMPTS:
                    retry.append((doc_id, doc))
                else:
                    failures.append(_describe_error(doc_id, item))
            if not retry:
                break
            pending = retry
            sleep(2.0**attempt)
        done = min(start + batch_size, len(docs))
        if progress:
            progress(index, done, len(docs))
    return failures


def inference_endpoint(client: Elasticsearch) -> str | None:
    """The inference endpoint `clips.text_semantic` uses, as Elastic reports it."""
    mapping = client.indices.get_mapping(index=CLIPS_INDEX)
    field = mapping[CLIPS_INDEX]["mappings"]["properties"].get("text_semantic", {})
    return field.get("inference_id")


def _print_progress(index: str, done: int, total: int) -> None:
    print(f"{index}: {done}/{total} indexed", file=sys.stderr, flush=True)


def run(
    client: Elasticsearch,
    chunks_path: Path,
    recreate: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: Progress | None = _print_progress,
) -> int:
    """Create the indexes and load `chunks_path`. Returns a process exit code."""
    started = time.monotonic()
    chunks = read_chunks(chunks_path)
    unique_ids = {c["chunk_id"] for c in chunks}
    if len(unique_ids) != len(chunks):
        print(
            f"warning: {len(chunks) - len(unique_ids)} duplicate chunk_ids; last one wins",
            file=sys.stderr,
        )
    talks = derive_talks(chunks)
    print(
        f"read {len(chunks)} chunks across {len(talks)} talks from {chunks_path}",
        file=sys.stderr,
        flush=True,
    )

    write_semantic = ensure_indexes(client, recreate)
    failures = bulk_index(
        client,
        TALKS_INDEX,
        [(t["talk_id"], t) for t in talks],
        batch_size=batch_size,
        progress=progress,
    )
    failures += bulk_index(
        client,
        CLIPS_INDEX,
        [(c["chunk_id"], clip_doc(c, write_semantic)) for c in chunks],
        batch_size=batch_size,
        progress=progress,
    )

    client.indices.refresh(index=f"{CLIPS_INDEX},{TALKS_INDEX}")
    clips_count = int(client.count(index=CLIPS_INDEX)["count"])
    talks_count = int(client.count(index=TALKS_INDEX)["count"])
    elapsed = time.monotonic() - started

    print(f"{CLIPS_INDEX}: {clips_count} docs in Elastic; {len(chunks)} chunks in file")
    print(f"{TALKS_INDEX}: {talks_count} docs in Elastic; {len(talks)} talks in file")
    print(f"text_semantic inference endpoint: {inference_endpoint(client) or 'not reported'}")
    print(f"load took {elapsed:.1f}s")
    for failure in failures[:20]:
        print(f"failed: {failure}", file=sys.stderr)
    if failures:
        print(f"{len(failures)} documents failed to index", file=sys.stderr)
    ok = not failures and clips_count == len(unique_ids) and talks_count == len(talks)
    return 0 if ok else 1


def client_from_env() -> Elasticsearch:
    """An Elasticsearch client built from ELASTIC_URL and ELASTIC_API_KEY."""
    url = os.environ.get("ELASTIC_URL", "").strip()
    api_key = os.environ.get("ELASTIC_API_KEY", "").strip()
    missing = [
        name for name, value in (("ELASTIC_URL", url), ("ELASTIC_API_KEY", api_key)) if not value
    ]
    if missing:
        raise SystemExit(f"missing env: {', '.join(missing)} (see .env.example)")
    return Elasticsearch(
        url,
        api_key=api_key,
        request_timeout=REQUEST_TIMEOUT_SEC,
        max_retries=5,
        retry_on_timeout=True,
        retry_on_status=(429, 502, 503, 504),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the Elastic indexes and load the chunks.")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS_PATH, help="chunks JSONL")
    parser.add_argument(
        "--recreate", action="store_true", help="drop and recreate both indexes first"
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    return run(client_from_env(), args.chunks, recreate=args.recreate, batch_size=args.batch_size)


if __name__ == "__main__":
    sys.exit(main())
