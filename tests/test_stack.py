"""Task 7: stack parsing. Fixture files in, tool ids out; unknown packages always listed."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api import routes_stack
from api.main import app
from api.stack import (
    HELM_REPOSITORY_HINTS,
    MAX_CONTENT_BYTES,
    FileType,
    StackParseError,
    analyze_dependency_file,
    build_index,
    detect_file_type,
    normalize_go,
    parse_chart_yaml,
    parse_dependency_file,
    parse_go_mod,
    parse_package_json,
    parse_requirements,
)
from ingest.tag import load_tools

FIXTURES = Path(__file__).parent / "fixtures"
TOOLS = load_tools(Path(__file__).parent.parent / "data" / "tools.yaml")
INDEX = build_index(TOOLS)
client = TestClient(app)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def analyze(name: str, file_name: str | None = None):
    return analyze_dependency_file(file_name or name, fixture(name), INDEX)


def assert_accounted(match, packages) -> None:
    """Every parsed package either maps to a tool or is listed as unknown."""
    names = [p.name for p in packages]
    assert set(match.unknown) <= set(names)
    assert match.package_count == len(names)
    matched = len(names) - len(match.unknown)
    assert matched >= len(match.tool_ids)


# --- file type ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("go.mod", FileType.GO),
        ("demo-go.mod", FileType.GO),
        ("services/api/go.mod", FileType.GO),
        ("package.json", FileType.NPM),
        ("demo-package.json", FileType.NPM),
        ("requirements.txt", FileType.PYPI),
        ("requirements-dev.txt", FileType.PYPI),
        ("Chart.yaml", FileType.HELM),
        ("charts\\app\\Chart.yaml", FileType.HELM),
    ],
)
def test_detect_file_type(name, expected) -> None:
    assert detect_file_type(name) is expected


@pytest.mark.parametrize("name", ["Pipfile", "pom.xml", "notes.txt", "values.yaml", "go.sum"])
def test_detect_file_type_rejects_unsupported(name) -> None:
    with pytest.raises(StackParseError):
        detect_file_type(name)


# --- go.mod ------------------------------------------------------------------------------


def test_go_mod_parses_require_blocks_and_skips_replace() -> None:
    packages = parse_go_mod(fixture("go.mod"))
    names = [p.name for p in packages]
    assert names[0] == "github.com/argoproj/argo-cd/v2"
    assert "github.com/beorn7/perks" in names  # indirect requirements are kept
    assert "k8s.io/api" not in names  # only in the replace block
    assert "github.com/acme-platform/release-gate" not in names  # the module line
    assert len(names) == 23


def test_go_mod_single_line_requires() -> None:
    content = (
        "module x\n\n"
        "require github.com/kedacore/keda/v2 v2.16.0\n"
        "require helm.sh/helm/v3 v3.16.3 // indirect\n"
        "require(\n\tgithub.com/kyverno/kyverno v1.13.2\n)\n"
        "tool (\n\tgolang.org/x/tools/cmd/stringer\n)\n"
    )
    assert [p.name for p in parse_go_mod(content)] == [
        "github.com/kedacore/keda/v2",
        "helm.sh/helm/v3",
        "github.com/kyverno/kyverno",
    ]


def test_demo_go_mod_maps_to_tools() -> None:
    match = analyze("demo-go.mod")
    assert match.file_type is FileType.GO
    assert match.tool_ids == [
        "argo-cd",
        "cert-manager",
        "envoy",
        "prometheus",
        "opentelemetry",
        "istio",
        "kubernetes",
    ]
    for ordinary in ("github.com/spf13/cobra", "go.uber.org/zap", "google.golang.org/grpc"):
        assert ordinary in match.unknown
    assert_accounted(match, parse_go_mod(fixture("demo-go.mod")))


def test_golden_go_mod_matches_demo() -> None:
    assert fixture("go.mod") == fixture("demo-go.mod")


@pytest.mark.parametrize(
    ("module", "tool_id"),
    [
        ("github.com/argoproj/argo-cd/v2", "argo-cd"),
        ("github.com/argoproj/argo-cd/v2/pkg/apiclient", "argo-cd"),
        ("go.opentelemetry.io/otel/sdk", "opentelemetry"),
        ("go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp", "opentelemetry"),
        ("github.com/argoproj/argo-workflows/v3/pkg/apiclient", "argo-workflows"),
        ("github.com/kedacore/keda/v3", "keda"),  # a future major version still matches
        ("github.com/IBM/sarama", "kafka"),
    ],
)
def test_go_prefix_matching(module, tool_id) -> None:
    match = analyze_dependency_file("go.mod", f"module x\nrequire {module} v1.0.0\n", INDEX)
    assert match.tool_ids == [tool_id]
    assert match.unknown == []


def test_go_prefix_matches_whole_segments_only() -> None:
    content = "module x\nrequire github.com/argoproj/argo-cd-extras v1.0.0\n"
    match = analyze_dependency_file("go.mod", content, INDEX)
    assert match.tool_ids == []
    assert match.unknown == ["github.com/argoproj/argo-cd-extras"]


def test_normalize_go_keeps_first_segment() -> None:
    assert normalize_go("helm.sh/helm/v3") == "helm.sh/helm"
    assert normalize_go("GitHub.com/IBM/sarama") == "github.com/ibm/sarama"


# --- package.json ------------------------------------------------------------------------


def test_demo_package_json_maps_scoped_and_plain_packages() -> None:
    match = analyze("demo-package.json")
    assert match.file_type is FileType.NPM
    assert match.tool_ids == ["opentelemetry", "prometheus"]
    # @opentelemetry/resources is not listed in tools.yaml: the scope owner claims it
    assert "@opentelemetry/resources" not in match.unknown
    assert match.unknown == [
        "express",
        "pino",
        "zod",
        "@types/express",
        "@types/node",
        "typescript",
        "vitest",
    ]
    assert_accounted(match, parse_package_json(fixture("demo-package.json")))


def test_package_json_reads_peer_dependencies() -> None:
    content = '{"peerDependencies": {"@backstage/core-plugin-api": "1.10.0", "react": "18.3.1"}}'
    match = analyze_dependency_file("package.json", content, INDEX)
    assert match.tool_ids == ["backstage"]
    assert match.unknown == ["react"]


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"dependencies": ["a"]}'])
def test_package_json_rejects_bad_input(content) -> None:
    with pytest.raises(StackParseError):
        parse_package_json(content)


# --- requirements.txt --------------------------------------------------------------------


def test_requirements_parses_names() -> None:
    names = [p.name for p in parse_requirements(fixture("requirements.txt"))]
    assert names == [
        "fastapi",
        "uvicorn",
        "opentelemetry-sdk",
        "opentelemetry_exporter_otlp_proto_http",
        "Prometheus_Client",
        "kubernetes",
        "psycopg",
        "hera",
        "requests",
        "fastapi",
    ]


def test_requirements_maps_to_tools() -> None:
    match = analyze("requirements.txt")
    assert match.file_type is FileType.PYPI
    assert match.tool_ids == [
        "opentelemetry",
        "prometheus",
        "kubernetes",
        "postgres",
        "argo-workflows",
    ]
    assert match.unknown == ["fastapi", "uvicorn", "requests"]  # duplicates listed once
    _, packages = parse_dependency_file("requirements.txt", fixture("requirements.txt"))
    assert_accounted(match, packages)


# --- Chart.yaml --------------------------------------------------------------------------


def test_chart_yaml_maps_names_and_repository_hints() -> None:
    match = analyze("Chart.yaml")
    assert match.file_type is FileType.HELM
    # trust-manager maps to cert-manager through charts.jetstack.io; Postgresql is
    # matched case-insensitively; ingress-nginx is unknown
    assert match.tool_ids == ["prometheus", "opentelemetry", "cert-manager", "postgres"]
    assert match.unknown == ["ingress-nginx"]
    assert_accounted(match, parse_chart_yaml(fixture("Chart.yaml")))


def test_helm_repository_hints_name_real_tools() -> None:
    assert set(HELM_REPOSITORY_HINTS.values()) <= {t.id for t in TOOLS}


@pytest.mark.parametrize(
    "content",
    [
        "a: [unclosed",
        "- just\n- a list",
        "dependencies: {name: x}",
        "dependencies:\n  - version: 1",
    ],
)
def test_chart_yaml_rejects_bad_input(content) -> None:
    with pytest.raises(StackParseError):
        parse_chart_yaml(content)


def test_chart_yaml_without_dependencies_is_empty() -> None:
    assert parse_chart_yaml("apiVersion: v2\nname: app\nversion: 0.1.0\n") == []


# --- tools.yaml --------------------------------------------------------------------------


def test_no_package_names_two_tools() -> None:
    for eco in ("go", "npm", "pypi", "helm"):
        owners: dict[str, str] = {}
        for tool in TOOLS:
            for name in tool.packages.get(eco, ()):
                key = normalize_go(name) if eco == "go" else name.lower()
                assert owners.setdefault(key, tool.id) == tool.id, f"{eco} {name}"


def test_content_size_limit() -> None:
    with pytest.raises(StackParseError):
        parse_dependency_file("go.mod", "x" * (MAX_CONTENT_BYTES + 1))


# --- POST /api/stack ---------------------------------------------------------------------


def test_route_picked_and_file_tools() -> None:
    response = client.post(
        "/api/stack",
        json={
            "tools": ["prometheus", "flux"],
            "dependency_file": {"name": "go.mod", "content": fixture("go.mod")},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tools"][:2] == [
        {"id": "prometheus", "name": "Prometheus", "source": "picked"},
        {"id": "flux", "name": "Flux", "source": "picked"},
    ]
    from_file = [t for t in body["tools"] if t["source"] == "go.mod"]
    assert [t["id"] for t in from_file] == [
        "argo-cd",
        "cert-manager",
        "envoy",
        "opentelemetry",
        "istio",
        "kubernetes",
    ]  # prometheus was already picked, so it appears once
    assert {"github.com/spf13/cobra", "go.uber.org/zap"} <= set(body["unknown_packages"])


def test_route_source_is_file_base_name() -> None:
    response = client.post(
        "/api/stack",
        json={
            "dependency_file": {
                "name": "web/demo-package.json",
                "content": fixture("demo-package.json"),
            }
        },
    )
    assert response.status_code == 200
    assert {t["source"] for t in response.json()["tools"]} == {"demo-package.json"}


def test_route_tools_only() -> None:
    response = client.post("/api/stack", json={"tools": ["istio"]})
    assert response.status_code == 200
    assert response.json() == {
        "tools": [{"id": "istio", "name": "Istio", "source": "picked"}],
        "unknown_packages": [],
    }


def test_route_empty_request() -> None:
    response = client.post("/api/stack", json={})
    assert response.status_code == 200
    assert response.json() == {"tools": [], "unknown_packages": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"tools": ["argo-cd", "not-a-tool"]},
        {"tools": "argo-cd"},
        {"dependency_file": {"name": "pom.xml", "content": "<project/>"}},
        {"dependency_file": {"name": "package.json", "content": "{oops"}},
        {"dependency_file": {"name": "Chart.yaml", "content": "a: [unclosed"}},
        {"dependency_file": {"name": "go.mod"}},
        {"dependency_file": {"name": "", "content": ""}},
        {"dependency_file": {"name": "go.mod", "content": "x" * (MAX_CONTENT_BYTES + 1)}},
        {"dependency_file": {"name": "go.mod", "content": "é" * (MAX_CONTENT_BYTES // 2 + 1)}},
    ],
)
def test_route_rejects_bad_input(payload) -> None:
    response = client.post("/api/stack", json=payload)
    assert response.status_code == 422


def test_route_unknown_tool_error_names_the_id() -> None:
    response = client.post("/api/stack", json={"tools": ["not-a-tool"]})
    assert response.status_code == 422
    assert "not-a-tool" in response.json()["detail"]


def test_route_span_carries_counts_only(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(routes_stack, "tracer", provider.get_tracer("debrief"))

    response = client.post(
        "/api/stack", json={"dependency_file": {"name": "go.mod", "content": fixture("go.mod")}}
    )
    assert response.status_code == 200

    (span,) = exporter.get_finished_spans()
    assert span.name == "stack.parse"
    attrs = dict(span.attributes or {})
    assert attrs["debrief.stack.file_type"] == "go"
    assert attrs["debrief.stack.package_count"] == 23
    assert attrs["debrief.stack.matched_tool_count"] == 7
    assert attrs["debrief.stack.unknown_count"] == len(response.json()["unknown_packages"])
    assert not any("github.com/spf13" in str(v) for v in attrs.values())  # no file contents
