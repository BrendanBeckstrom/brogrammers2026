import json
from pathlib import Path

import pytest

from ingest.tag import (
    Tool,
    chunks_per_tool,
    load_tools,
    main,
    tag_chunks,
    tag_text,
)

TOOLS_YAML = Path(__file__).resolve().parent.parent / "data" / "tools.yaml"


@pytest.fixture(scope="module")
def tools() -> list[Tool]:
    return load_tools(TOOLS_YAML)


# --- the dictionary itself -------------------------------------------------------------


def test_dictionary_has_acceptance_ids(tools):
    ids = {t.id for t in tools}
    assert {"argo-cd", "prometheus", "opentelemetry", "istio", "envoy"} <= ids
    assert len([t for t in tools if not t.exclude_from_matching]) >= 30


def test_kubernetes_is_excluded(tools):
    by_id = {t.id: t for t in tools}
    assert by_id["kubernetes"].exclude_from_matching


def test_every_tool_has_all_package_ecosystems(tools):
    for tool in tools:
        assert set(tool.packages) == {"go", "npm", "pypi", "helm"}, tool.id


def test_known_packages_map_to_tools(tools):
    by_id = {t.id: t for t in tools}
    assert "@opentelemetry/api" in by_id["opentelemetry"].packages["npm"]
    assert "opentelemetry-sdk" in by_id["opentelemetry"].packages["pypi"]
    assert "prometheus-client" in by_id["prometheus"].packages["pypi"]
    assert "psycopg" in by_id["postgres"].packages["pypi"]
    assert "argo-cd" in by_id["argo-cd"].packages["helm"]


def test_every_term_tags_only_its_own_tool(tools):
    # No alias or caption spelling may also name another tool (e.g. a bare "argo").
    for tool in tools:
        if tool.exclude_from_matching:
            continue
        assert tool.terms, tool.id
        for term in tool.terms:
            assert tag_text(term, tools) == [tool.id], (tool.id, term)


# --- tag_text ---------------------------------------------------------------------------


def test_whole_words_only(tools):
    assert tag_text("We scrape everything with Prometheus.", tools) == ["prometheus"]
    assert tag_text("a promethean effort", tools) == []
    assert tag_text("the payload is opaque to the proxy", tools) == []  # not "opa"
    assert tag_text("an envoys meeting", tools) == []  # plurals are not implied
    assert tag_text("Istio's ambient mode", tools) == ["istio"]


def test_case_insensitive(tools):
    assert tag_text("ISTIO and envoy", tools) == ["envoy", "istio"]
    assert tag_text("OpenTelemetry", tools) == ["opentelemetry"]
    assert tag_text("OTEL collector", tools) == ["opentelemetry"]


def test_multi_word_aliases_with_space_hyphen_or_nothing(tools):
    for text in ["Argo CD", "argo-cd", "ArgoCD", "argo\ncd", "argo  CD"]:
        assert tag_text(f"we deploy with {text} today", tools) == ["argo-cd"], text
    assert tag_text("open telemetry collector", tools) == ["opentelemetry"]
    assert tag_text("cert-manager-webhook crashed", tools) == ["cert-manager"]


def test_bare_argo_is_ambiguous_and_matches_nothing(tools):
    assert tag_text("we use Argo for everything", tools) == []
    assert tag_text("Argo Workflows runs the batch jobs", tools) == ["argo-workflows"]
    assert tag_text("Argo CD syncs and Argo Rollouts canaries", tools) == [
        "argo-cd",
        "argo-rollouts",
    ]


def test_excluded_tools_never_match(tools):
    assert tag_text("Kubernetes, k8s, kubectl everywhere", tools) == []
    custom = [Tool(id="foo", name="Foo", aliases=("foo",), exclude_from_matching=True)]
    assert tag_text("foo", custom) == []


def test_caption_spellings_match(tools):
    assert tag_text("we decided to use Argo City", tools) == ["argo-cd"]
    assert tag_text("Psyllium is a CNI", tools) == ["cilium"]
    assert tag_text("the hotel collector receives spans", tools) == ["opentelemetry"]
    assert tag_text("we booked a hotel near the venue", tools) == []


def test_result_is_sorted_and_unique(tools):
    text = "Prometheus, Grafana, prometheus again, and Envoy. Grafana!"
    assert tag_text(text, tools) == ["envoy", "grafana", "prometheus"]


def test_empty_text(tools):
    assert tag_text("", tools) == []


def test_tool_without_terms_never_matches():
    assert tag_text("anything", [Tool(id="bare", name="Bare")]) == []


# --- chunks and CLI ---------------------------------------------------------------------


def test_tag_chunks_keeps_other_fields(tools):
    chunks = [{"chunk_id": "a-1", "text": "Istio and Envoy", "tools": ["stale"], "start_sec": 1}]
    tagged = tag_chunks(chunks, tools)
    assert tagged == [
        {"chunk_id": "a-1", "text": "Istio and Envoy", "tools": ["envoy", "istio"], "start_sec": 1}
    ]
    assert chunks[0]["tools"] == ["stale"]  # input untouched


def test_chunks_per_tool_sorted_descending(tools):
    chunks = [{"tools": ["envoy", "istio"]}, {"tools": ["istio"]}, {"tools": []}]
    counts = chunks_per_tool(chunks, tools)
    assert counts[:2] == [("istio", 2), ("envoy", 1)]
    assert ("kubernetes", 0) not in counts
    assert all(a[1] >= b[1] for a, b in zip(counts, counts[1:], strict=False))


def test_cli_rewrites_chunks_and_prints_report(tmp_path, capsys):
    path = tmp_path / "chunks.jsonl"
    rows = [
        {"chunk_id": f"t-{i}", "text": "Argo CD with Prometheus", "tools": []} for i in range(5)
    ] + [{"chunk_id": "t-9", "text": "nothing relevant here", "tools": ["x"]}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    assert main(["--chunks", str(path), "--tools", str(TOOLS_YAML)]) == 0

    written = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["tools"] for r in written] == [["argo-cd", "prometheus"]] * 5 + [[]]
    assert [r["chunk_id"] for r in written] == [r["chunk_id"] for r in rows]
    out = capsys.readouterr().out
    assert "argo-cd" in out and "6 chunks tagged" in out
    assert "2 of" in out and "tools have >= 5 chunks" in out


def test_cli_reads_paths_from_env(tmp_path, monkeypatch, capsys):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps({"chunk_id": "a", "text": "Cilium"}) + "\n")
    monkeypatch.setenv("DEBRIEF_CHUNKS", str(path))
    monkeypatch.setenv("DEBRIEF_TOOLS", str(TOOLS_YAML))
    assert main([]) == 0
    assert json.loads(path.read_text())["tools"] == ["cilium"]


def test_cli_missing_chunks_file(tmp_path, capsys):
    assert main(["--chunks", str(tmp_path / "nope.jsonl"), "--tools", str(TOOLS_YAML)]) == 1


# --- load_tools validation --------------------------------------------------------------


def test_load_tools_rejects_duplicates(tmp_path):
    path = tmp_path / "tools.yaml"
    path.write_text("- {id: a, name: A}\n- {id: a, name: A again}\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_tools(path)


def test_load_tools_rejects_bad_id(tmp_path):
    path = tmp_path / "tools.yaml"
    path.write_text("- {id: Argo_CD, name: Argo CD}\n")
    with pytest.raises(ValueError, match="kebab-case"):
        load_tools(path)
