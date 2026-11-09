"""observability/dash0: the dashboard and SLO files stay well-formed and name real metrics."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DASH0 = ROOT / "observability" / "dash0"
DASHBOARD = DASH0 / "debrief-health.dashboard.yaml"
SLOS = sorted(DASH0.glob("slo-*.openslo.yaml"))
METRIC_NAME = re.compile(r'otel_metric_name="([^"]+)"')


def _queries(node: object) -> list[str]:
    if isinstance(node, dict):
        return [q for k, v in node.items() for q in ([v] if k == "query" else _queries(v))]
    if isinstance(node, list):
        return [q for item in node for q in _queries(item)]
    return []


def test_dashboard_json_matches_yaml() -> None:
    cr = yaml.safe_load(DASHBOARD.read_text())
    exported = json.loads((DASH0 / "debrief-health.dashboard.json").read_text())
    assert exported == {
        "kind": "Dashboard",
        "metadata": {"name": "debrief-health"},
        "spec": cr["spec"],
    }, "run: uv run python observability/dash0/to_json.py"


def test_every_panel_is_placed_once_on_a_24_wide_grid() -> None:
    spec = yaml.safe_load(DASHBOARD.read_text())["spec"]
    placed = []
    for layout in spec["layouts"]:
        for item in layout["spec"]["items"]:
            assert item["x"] + item["width"] <= 24
            placed.append(item["content"]["$ref"].removeprefix("#/spec/panels/"))
    assert sorted(placed) == sorted(spec["panels"])
    names = {p["spec"]["display"]["name"] for p in spec["panels"].values()}
    assert {
        "Coverage gaps (empty playlists by tool)",
        "Most requested tools (top 10)",
        "Clip label mix",
        "Rerank batch outcomes",
        "Memo latency p50 / p95",
        "Playlists shared",
        "HTTP requests by route and status (req/s)",
    } <= names


def test_app_metric_panels_use_a_fixed_five_minute_window() -> None:
    """App and browser metrics arrive every 10-60 s, so Dash0's short $__rate_interval can hold
    one sample and rate()/increase() return nothing. Only Dash0's own span metric keeps it."""
    panels = yaml.safe_load(DASHBOARD.read_text())["spec"]["panels"]
    queries = [
        q["spec"]["plugin"]["spec"]["query"]
        for p in panels.values()
        for q in p["spec"].get("queries", [])
    ]
    assert queries
    for query in queries:
        if "dash0.spans" in query:
            continue
        assert "$__rate_interval" not in query, query
        assert "[5m]" in query, query
    assert "playlist_served_logs" not in panels and "cache_lookups" not in panels


def test_queries_name_metrics_the_api_exports() -> None:
    exported = set(
        re.findall(r'"(debrief\.[a-z_.]+)"', (ROOT / "api" / "telemetry.py").read_text())
    )
    files = [DASHBOARD, *SLOS]
    used = {
        m
        for f in files
        for q in _queries(yaml.safe_load(f.read_text()))
        for m in METRIC_NAME.findall(q)
    }
    assert {m for m in used if m.startswith("debrief.")} <= exported
    assert {
        "debrief.playlist.tool_requests",
        "debrief.playlist.clip_labels",
        "debrief.rerank.batches",
        "debrief.memo.duration",
        "debrief.playlists.shared",
    } <= used
    # Dash0's own span/web metrics, and the FastAPI instrumentation's request histogram.
    assert used - exported <= {"dash0.spans", "dash0.web.events", "http.server.duration"}


def test_slos() -> None:
    assert [p.name for p in SLOS] == [
        "slo-memo-citations.openslo.yaml",
        "slo-playlists-fast.openslo.yaml",
    ]
    targets = {}
    for path in SLOS:
        slo = yaml.safe_load(path.read_text())
        assert (slo["apiVersion"], slo["kind"]) == ("openslo/v1", "SLO")
        spec = slo["spec"]
        assert spec["budgetingMethod"] == "Occurrences"
        assert spec["timeWindow"] == [{"duration": "28d", "isRolling": True}]
        ratio = spec["indicator"]["spec"]["ratioMetric"]
        for key in ("good", "bad", "total"):
            if key in ratio and ratio["counter"]:
                # Counter SLOs take bare selectors; Dash0 applies increase() itself.
                assert re.fullmatch(r"\{[^{}()]*\}", ratio[key]["metricSource"]["spec"]["query"])
        targets[slo["metadata"]["name"]] = spec["objectives"][0]["target"]
    assert targets == {"debrief-memo-citations": 0.99, "debrief-playlists-fast": 0.95}
