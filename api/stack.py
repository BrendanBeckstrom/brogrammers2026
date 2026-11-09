"""Stack parsing (task 7): dependency files in, tool ids out. Pure functions, no I/O.

Supported files, picked by name (a prefix such as "demo-go.mod" is fine):

- `go.mod`: `require` blocks and single-line requires. `replace`, `exclude`, `retract`,
  `tool` and `godebug` directives are skipped. `// indirect` requirements are kept and
  treated like direct ones: a tool you only reach indirectly is still in your build, and
  an unrecognised indirect module is still listed, so nothing is dropped silently.
- `package.json`: keys of dependencies, devDependencies, peerDependencies and
  optionalDependencies.
- `requirements*.txt`: one requirement per line; extras, version specifiers, environment
  markers, `name @ url` and inline comments are stripped; option lines (`-r`, `-e`, `-c`,
  `--index-url`, ...) and bare URLs or paths are skipped.
- `Chart.yaml`: `dependencies[].name`. A dependency whose name is not known can still be
  matched through its repository host when that repository serves one project's charts
  (e.g. charts.jetstack.io -> cert-manager).

Mapping uses `packages` in data/tools.yaml:

- go: path prefix, segment-wise, longest wins; `/vN` major-version segments are ignored on
  both sides, so `github.com/argoproj/argo-cd/v2/pkg/apiclient` maps to argo-cd.
- npm: exact name; an entry ending in "/" is a prefix. A scoped package (`@scope/x`) not
  listed exactly matches the tool that owns that scope, when exactly one tool lists
  packages in it (so `@opentelemetry/resources` maps to opentelemetry).
- pypi: exact name after PEP 503 normalisation (case, and runs of `-_.` are equal).
- helm: exact chart name, case-insensitive, then the repository hint above.

Every package that maps to no tool is returned in `unknown`, in file order.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import urlparse

import yaml

MAX_CONTENT_BYTES = 200_000


class FileType(StrEnum):
    """The ecosystem of a dependency file; values match the keys of `packages`."""

    GO = "go"
    NPM = "npm"
    PYPI = "pypi"
    HELM = "helm"


class StackParseError(ValueError):
    """The dependency file is unsupported or malformed. The message never quotes content."""


class ToolLike(Protocol):
    """What the mapper needs from a tools.yaml entry (ingest.tag.Tool satisfies it)."""

    @property
    def id(self) -> str: ...

    @property
    def packages(self) -> Mapping[str, Sequence[str]]: ...


@dataclass(frozen=True)
class Package:
    """One dependency read from a file. `repository` is only set for Helm charts."""

    name: str
    repository: str = ""


@dataclass(frozen=True)
class StackMatch:
    """Result of mapping one file: tool ids in file order, and every unmatched package."""

    file_type: FileType
    package_count: int
    tool_ids: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


# Helm repositories that serve one project's charts, so the host identifies the tool even
# when the chart name is not listed in tools.yaml (e.g. trust-manager on charts.jetstack.io).
# Multi-project repos (argoproj, grafana, bitnami, ...) are deliberately absent.
HELM_REPOSITORY_HINTS: dict[str, str] = {
    "charts.jetstack.io": "cert-manager",
    "prometheus-community.github.io": "prometheus",
    "open-telemetry.github.io": "opentelemetry",
    "istio-release.storage.googleapis.com": "istio",
    "helm.cilium.io": "cilium",
    "helm.linkerd.io": "linkerd",
    "kyverno.github.io": "kyverno",
    "kedacore.github.io": "keda",
    "falcosecurity.github.io": "falco",
    "open-policy-agent.github.io": "opa",
    "charts.crossplane.io": "crossplane",
    "cloudnative-pg.github.io": "postgres",
    "strimzi.io": "kafka",
    "dapr.github.io": "dapr",
    "fluxcd-community.github.io": "flux",
    "jaegertracing.github.io": "jaeger",
}

_GO_MAJOR = re.compile(r"^v[0-9]+$")
_PEP503 = re.compile(r"[-_.]+")
_REQ_NAME = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_URLISH = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|\.{0,2}/|~)", re.IGNORECASE)


# --- file type ---------------------------------------------------------------------------


def detect_file_type(name: str) -> FileType:
    """Pick the parser from the file name. Raises StackParseError for anything else."""
    base = PurePosixPath(name.replace("\\", "/")).name.lower()
    if base.endswith("go.mod"):
        return FileType.GO
    if base.endswith("package.json"):
        return FileType.NPM
    if base.endswith(("chart.yaml", "chart.yml")):
        return FileType.HELM
    if base.endswith(".txt") and "requirements" in base:
        return FileType.PYPI
    raise StackParseError(
        "unsupported dependency file: send go.mod, package.json, requirements.txt or Chart.yaml"
    )


def display_name(name: str) -> str:
    """The file's base name, used as the `source` of tools found in it."""
    return PurePosixPath(name.replace("\\", "/")).name or name


# --- parsers -----------------------------------------------------------------------------


def _strip_go_comment(line: str) -> str:
    return line.split("//", 1)[0].strip()


def parse_go_mod(content: str) -> list[Package]:
    """Module paths from every `require` directive, in file order."""
    packages: list[Package] = []
    block: str | None = None
    for raw in content.splitlines():
        line = _strip_go_comment(raw)
        if not line:
            continue
        if block is not None:
            if line == ")":
                block = None
            elif block == "require":
                packages.append(Package(line.split()[0].strip('"')))
            continue
        if line.endswith("("):  # "require (" or "require("
            block = line[:-1].strip()
            continue
        words = line.split()
        verb = words[0]
        if verb == "require" and len(words) >= 2:
            packages.append(Package(words[1].strip('"')))
    return packages


def parse_package_json(content: str) -> list[Package]:
    """Package names from dependencies, devDependencies, peerDependencies, optionalDependencies."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise StackParseError(f"package.json is not valid JSON (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise StackParseError("package.json must be a JSON object")
    packages: list[Package] = []
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise StackParseError(f"package.json {key} must be an object")
        packages.extend(Package(str(name).strip()) for name in section if str(name).strip())
    return packages


def _join_continuations(content: str) -> list[str]:
    lines: list[str] = []
    pending = ""
    for raw in content.splitlines():
        if raw.rstrip().endswith("\\"):
            pending += raw.rstrip()[:-1] + " "
            continue
        lines.append(pending + raw)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def parse_requirements(content: str) -> list[Package]:
    """Distribution names from a pip requirements file, as written (not yet normalised)."""
    packages: list[Package] = []
    for raw in _join_continuations(content):
        line = raw.strip()
        if line.startswith("#"):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line or line.startswith("-") or _URLISH.match(line):
            continue
        match = _REQ_NAME.match(line)
        if match:
            packages.append(Package(match.group(1)))
    return packages


def parse_chart_yaml(content: str) -> list[Package]:
    """Chart names (with their repository) from a Helm Chart.yaml `dependencies` list."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        raise StackParseError("Chart.yaml is not valid YAML") from None
    if data is None:
        return []
    if not isinstance(data, dict):
        raise StackParseError("Chart.yaml must be a YAML mapping")
    deps = data.get("dependencies") or []
    if not isinstance(deps, list):
        raise StackParseError("Chart.yaml dependencies must be a list")
    packages: list[Package] = []
    for dep in deps:
        if not isinstance(dep, dict) or not isinstance(dep.get("name"), str):
            raise StackParseError("every Chart.yaml dependency needs a name")
        repository = dep.get("repository")
        packages.append(
            Package(dep["name"].strip(), repository if isinstance(repository, str) else "")
        )
    return [p for p in packages if p.name]


PARSERS = {
    FileType.GO: parse_go_mod,
    FileType.NPM: parse_package_json,
    FileType.PYPI: parse_requirements,
    FileType.HELM: parse_chart_yaml,
}


def parse_dependency_file(name: str, content: str) -> tuple[FileType, list[Package]]:
    """Detect the file type from its name and parse it. Packages are unique, in file order."""
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise StackParseError(f"dependency file is larger than {MAX_CONTENT_BYTES} bytes")
    file_type = detect_file_type(name)
    seen: set[str] = set()
    unique: list[Package] = []
    for package in PARSERS[file_type](content):
        if package.name not in seen:
            seen.add(package.name)
            unique.append(package)
    return file_type, unique


# --- mapping -----------------------------------------------------------------------------


def normalize_go(path: str) -> str:
    """Lowercase, and drop `/vN` major-version segments (never the first segment)."""
    parts = path.strip().lower().split("/")
    return "/".join(parts[:1] + [p for p in parts[1:] if not _GO_MAJOR.match(p)])


def normalize_pypi(name: str) -> str:
    """PEP 503 name normalisation."""
    return _PEP503.sub("-", name.strip()).lower()


def _npm_scope(name: str) -> str | None:
    return name.split("/", 1)[0] if name.startswith("@") and "/" in name else None


@dataclass(frozen=True)
class PackageIndex:
    """Lookup tables built once from tools.yaml."""

    go: dict[str, str]
    npm: dict[str, str]
    npm_prefixes: dict[str, str]
    npm_scopes: dict[str, str]
    pypi: dict[str, str]
    helm: dict[str, str]
    tool_ids: frozenset[str]


def build_index(tools: Iterable[ToolLike]) -> PackageIndex:
    """Index every package name in tools.yaml. The first tool to list a name keeps it."""
    go: dict[str, str] = {}
    npm: dict[str, str] = {}
    npm_prefixes: dict[str, str] = {}
    scope_owners: dict[str, set[str]] = {}
    pypi: dict[str, str] = {}
    helm: dict[str, str] = {}
    ids: set[str] = set()
    for tool in tools:
        ids.add(tool.id)
        packages = tool.packages
        for name in packages.get("go", ()):
            go.setdefault(normalize_go(name), tool.id)
        for name in packages.get("npm", ()):
            key = name.strip().lower()
            if key.endswith("/"):
                npm_prefixes.setdefault(key, tool.id)
            else:
                npm.setdefault(key, tool.id)
            scope = _npm_scope(key)
            if scope:
                scope_owners.setdefault(scope, set()).add(tool.id)
        for name in packages.get("pypi", ()):
            pypi.setdefault(normalize_pypi(name), tool.id)
        for name in packages.get("helm", ()):
            helm.setdefault(name.strip().lower(), tool.id)
    npm_scopes = {s: next(iter(owners)) for s, owners in scope_owners.items() if len(owners) == 1}
    return PackageIndex(go, npm, npm_prefixes, npm_scopes, pypi, helm, frozenset(ids))


def _match_go(path: str, index: PackageIndex) -> str | None:
    parts = normalize_go(path).split("/")
    for end in range(len(parts), 0, -1):
        tool_id = index.go.get("/".join(parts[:end]))
        if tool_id:
            return tool_id
    return None


def _match_npm(name: str, index: PackageIndex) -> str | None:
    key = name.lower()
    if key in index.npm:
        return index.npm[key]
    for prefix in sorted(index.npm_prefixes, key=len, reverse=True):
        if key.startswith(prefix):
            return index.npm_prefixes[prefix]
    scope = _npm_scope(key)
    return index.npm_scopes.get(scope) if scope else None


def _match_helm(package: Package, index: PackageIndex) -> str | None:
    tool_id = index.helm.get(package.name.lower())
    if tool_id:
        return tool_id
    host = (urlparse(package.repository.strip()).hostname or "").lower()
    hinted = HELM_REPOSITORY_HINTS.get(host)
    return hinted if hinted in index.tool_ids else None


def match_package(file_type: FileType, package: Package, index: PackageIndex) -> str | None:
    """The tool id a package belongs to, or None."""
    if file_type is FileType.GO:
        return _match_go(package.name, index)
    if file_type is FileType.NPM:
        return _match_npm(package.name, index)
    if file_type is FileType.PYPI:
        return index.pypi.get(normalize_pypi(package.name))
    return _match_helm(package, index)


def map_packages(
    file_type: FileType, packages: Sequence[Package], index: PackageIndex
) -> StackMatch:
    """Map parsed packages to tool ids (unique, file order); collect every unmatched one."""
    tool_ids: list[str] = []
    unknown: list[str] = []
    for package in packages:
        tool_id = match_package(file_type, package, index)
        if tool_id is None:
            unknown.append(package.name)
        elif tool_id not in tool_ids:
            tool_ids.append(tool_id)
    return StackMatch(file_type, len(packages), tool_ids, unknown)


def analyze_dependency_file(name: str, content: str, index: PackageIndex) -> StackMatch:
    """Parse a dependency file and map it to tools in one call."""
    file_type, packages = parse_dependency_file(name, content)
    return map_packages(file_type, packages, index)


def unknown_tool_ids(ids: Iterable[str], index: PackageIndex) -> list[str]:
    """Requested tool ids that tools.yaml does not define, in request order."""
    return [i for i in dict.fromkeys(ids) if i not in index.tool_ids]
