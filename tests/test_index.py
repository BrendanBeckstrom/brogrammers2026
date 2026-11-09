import json
from pathlib import Path
from typing import Any

import pytest
from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
from elasticsearch import BadRequestError

from ingest import index as idx


def _bad_request(message: str) -> BadRequestError:
    meta = ApiResponseMeta(
        status=400,
        http_version="1.1",
        headers=HttpHeaders(),
        duration=0.0,
        node=NodeConfig("https", "localhost", 443),
    )
    return BadRequestError(message=message, meta=meta, body={"error": {"reason": message}})


class FakeIndices:
    def __init__(self, client: "FakeClient", refuse_copy_to: bool) -> None:
        self.client = client
        self.refuse_copy_to = refuse_copy_to
        self.mappings: dict[str, dict[str, Any]] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []

    def exists(self, index: str) -> bool:
        return index in self.mappings

    def create(self, index: str, mappings: dict[str, Any]) -> dict[str, Any]:
        if self.refuse_copy_to and "copy_to" in json.dumps(mappings):
            raise _bad_request("copy_to is not supported for semantic_text targets")
        self.mappings[index] = mappings
        self.created.append(index)
        return {"acknowledged": True}

    def delete(self, index: str, ignore_unavailable: bool = False) -> dict[str, Any]:
        assert ignore_unavailable
        self.deleted.append(index)
        self.mappings.pop(index, None)
        self.client.docs.pop(index, None)
        return {"acknowledged": True}

    def get_mapping(self, index: str) -> dict[str, Any]:
        mapping = json.loads(json.dumps(self.mappings[index]))
        semantic = mapping["properties"].get("text_semantic")
        if semantic is not None:  # Elastic fills in the default endpoint.
            semantic.setdefault("inference_id", ".fake-default")
        return {index: {"mappings": mapping}}

    def refresh(self, index: str) -> dict[str, Any]:
        return {}


class FakeClient:
    """Records what the loader sends; `item_statuses` scripts per-item bulk statuses."""

    def __init__(
        self,
        refuse_copy_to: bool = False,
        item_statuses: list[list[int]] | None = None,
    ) -> None:
        self.indices = FakeIndices(self, refuse_copy_to)
        self.docs: dict[str, dict[str, dict[str, Any]]] = {}
        self.bulk_calls: list[list[dict[str, Any]]] = []
        self.item_statuses = list(item_statuses or [])

    def bulk(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        self.bulk_calls.append(operations)
        statuses = self.item_statuses.pop(0) if self.item_statuses else None
        items = []
        for n, (action, doc) in enumerate(zip(operations[::2], operations[1::2], strict=True)):
            meta = action["index"]
            status = statuses[n] if statuses else 201
            item: dict[str, Any] = {"_id": meta["_id"], "status": status}
            if status < 300:
                self.docs.setdefault(meta["_index"], {})[meta["_id"]] = doc
            else:
                item["error"] = {
                    "type": "x_exception",
                    "reason": "busy. Preview of field's value: 'secret transcript'",
                }
            items.append({"index": item})
        return {"errors": any(i["index"]["status"] >= 300 for i in items), "items": items}

    def count(self, index: str) -> dict[str, int]:
        return {"count": len(self.docs.get(index, {}))}


def _chunk(talk_id: str, start: int, **extra: Any) -> dict[str, Any]:
    chunk = {
        "chunk_id": f"{talk_id}-{start}",
        "talk_id": talk_id,
        "youtube_id": talk_id,
        "title": f"Talk {talk_id}",
        "speakers": ["Ada"],
        "track": "Observability",
        "start_sec": start,
        "end_sec": start + 75,
        "text": f"text of {talk_id} at {start}",
        "tools": ["argo-cd"],
        "talk_label": "deep_dive",
    }
    chunk.update(extra)
    return chunk


def _write_chunks(path: Path, chunks: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(c) + "\n" for c in chunks), encoding="utf-8")
    return path


def test_ensure_indexes_sends_the_brief_mapping() -> None:
    client = FakeClient()
    write_semantic = idx.ensure_indexes(client, recreate=False)

    assert write_semantic is False
    clips = client.indices.mappings["clips"]["properties"]
    assert clips["text"] == {"type": "text", "copy_to": "text_semantic"}
    assert clips["text_semantic"] == {"type": "semantic_text"}  # default inference endpoint
    assert clips["tools"] == {"type": "keyword"}
    assert clips["start_sec"] == {"type": "integer"}
    assert set(clips) == set(idx.CHUNK_FIELDS) | {"text_semantic"}
    talks = client.indices.mappings["talks"]["properties"]
    assert set(talks) == set(idx.TALK_FIELDS)
    assert talks["title"] == {"type": "text"}
    assert talks["talk_id"] == {"type": "keyword"}


def test_existing_indexes_are_kept_unless_recreate() -> None:
    client = FakeClient()
    idx.ensure_indexes(client, recreate=False)
    idx.ensure_indexes(client, recreate=False)
    assert client.indices.created == ["clips", "talks"]
    assert client.indices.deleted == []

    idx.ensure_indexes(client, recreate=True)
    assert client.indices.deleted == ["clips", "talks"]
    assert client.indices.created == ["clips", "talks", "clips", "talks"]


def test_copy_to_refused_falls_back_to_writing_both_fields(tmp_path: Path) -> None:
    client = FakeClient(refuse_copy_to=True)
    path = _write_chunks(tmp_path / "chunks.jsonl", [_chunk("t1", 0)])

    assert idx.run(client, path, progress=None) == 0
    assert "copy_to" not in json.dumps(client.indices.mappings["clips"])
    doc = client.docs["clips"]["t1-0"]
    assert doc["text_semantic"] == doc["text"] == "text of t1 at 0"
    # A second run against the existing index still writes both fields.
    assert idx.ensure_indexes(client, recreate=False) is True


def test_bulk_actions_use_chunk_id_and_batch(tmp_path: Path) -> None:
    chunks = [_chunk("t1", s) for s in (0, 60, 120)] + [_chunk("t2", s) for s in (0, 60)]
    path = _write_chunks(tmp_path / "chunks.jsonl", chunks)
    client = FakeClient()

    assert idx.run(client, path, batch_size=2, progress=None) == 0

    clip_calls = [ops for ops in client.bulk_calls if ops[0]["index"]["_index"] == "clips"]
    assert [len(ops) // 2 for ops in clip_calls] == [2, 2, 1]
    sent = [op for ops in clip_calls for op in ops]
    for action, doc in zip(sent[::2], sent[1::2], strict=True):
        assert action == {"index": {"_index": "clips", "_id": doc["chunk_id"]}}
        assert "text_semantic" not in doc  # copy_to fills it
    assert set(client.docs["clips"]) == {c["chunk_id"] for c in chunks}
    assert client.docs["clips"]["t2-60"] == chunks[4]


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    path = _write_chunks(tmp_path / "chunks.jsonl", [_chunk("t1", 0), _chunk("t1", 60)])
    client = FakeClient()
    assert idx.run(client, path, progress=None) == 0
    assert idx.run(client, path, progress=None) == 0
    assert client.count(index="clips") == {"count": 2}
    assert client.count(index="talks") == {"count": 1}


def test_extra_fields_are_not_indexed() -> None:
    doc = idx.clip_doc(_chunk("t1", 0, debug="x"), write_semantic=False)
    assert set(doc) == set(idx.CHUNK_FIELDS)


def test_derive_talks_one_doc_per_talk() -> None:
    chunks = [
        _chunk("t1", 0, title="", speakers=[]),
        _chunk("t2", 0, title="Second", speakers=["Bo"], track="Security", talk_label="intro"),
        _chunk("t1", 60, title="First", speakers=["Ada", "Cy"]),
    ]
    talks = idx.derive_talks(chunks)

    assert [t["talk_id"] for t in talks] == ["t1", "t2"]
    assert talks[0] == {
        "talk_id": "t1",
        "youtube_id": "t1",
        "title": "First",  # first non-empty value wins
        "speakers": ["Ada", "Cy"],
        "track": "Observability",
        "talk_label": "deep_dive",
    }
    assert talks[1]["track"] == "Security"
    assert talks[1]["talk_label"] == "intro"


def test_talks_are_indexed_by_talk_id(tmp_path: Path) -> None:
    path = _write_chunks(tmp_path / "chunks.jsonl", [_chunk("t1", 0), _chunk("t2", 0)])
    client = FakeClient()
    assert idx.run(client, path, progress=None) == 0
    assert set(client.docs["talks"]) == {"t1", "t2"}
    assert "text" not in client.docs["talks"]["t1"]


def test_retryable_item_failures_are_resent() -> None:
    client = FakeClient(item_statuses=[[201, 429, 201], [201]])
    docs = [(f"c{n}", {"chunk_id": f"c{n}"}) for n in range(3)]
    sleeps: list[float] = []

    failures = idx.bulk_index(client, "clips", docs, sleep=sleeps.append)

    assert failures == []
    assert [len(ops) // 2 for ops in client.bulk_calls] == [3, 1]
    assert client.bulk_calls[1][0] == {"index": {"_index": "clips", "_id": "c1"}}
    assert set(client.docs["clips"]) == {"c0", "c1", "c2"}
    assert len(sleeps) == 1


def test_permanent_failures_are_reported_without_field_values(tmp_path: Path) -> None:
    client = FakeClient(item_statuses=[[400]])
    failures = idx.bulk_index(client, "clips", [("c0", {"chunk_id": "c0"})], sleep=lambda s: None)
    assert len(failures) == 1
    assert failures[0].startswith("c0: status 400 x_exception")
    assert "secret transcript" not in failures[0]


def test_run_fails_when_a_doc_is_missing(tmp_path: Path) -> None:
    path = _write_chunks(tmp_path / "chunks.jsonl", [_chunk("t1", 0)])
    client = FakeClient(item_statuses=[[201], [400]])  # talks ok, the clip rejected
    assert idx.run(client, path, progress=None) == 1


def test_read_chunks_skips_blank_lines_and_rejects_bad_ones(tmp_path: Path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(json.dumps(_chunk("t1", 0)) + "\n\n" + json.dumps(_chunk("t1", 60)) + "\n")
    assert [c["chunk_id"] for c in idx.read_chunks(path)] == ["t1-0", "t1-60"]

    path.write_text(json.dumps(_chunk("t1", 0)) + "\n{not json\n")
    with pytest.raises(ValueError, match=":2:"):
        idx.read_chunks(path)

    path.write_text(json.dumps({"talk_id": "t1"}) + "\n")
    with pytest.raises(ValueError, match="chunk_id"):
        idx.read_chunks(path)


def test_inference_endpoint_reads_the_mapping() -> None:
    client = FakeClient()
    idx.ensure_indexes(client, recreate=False)
    assert idx.inference_endpoint(client) == ".fake-default"


def test_client_from_env_requires_both_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTIC_URL", "https://example.invalid:443")
    monkeypatch.delenv("ELASTIC_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="ELASTIC_API_KEY"):
        idx.client_from_env()
