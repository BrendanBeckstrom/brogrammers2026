"""POST /api/stack (task 7): picked tool ids and/or a dependency file -> the team's tools.

Contract (docs/BRIEF.md, API): request `{"tools": [ids], "dependency_file": {"name",
"content"}}`, either field optional; response `{"tools": [{"id", "name", "source"}],
"unknown_packages": [strings]}`. `source` is "picked" or the dependency file's name.
Unknown tool ids and unsupported or malformed files are rejected with 422.
"""

import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from opentelemetry import trace
from pydantic import BaseModel, Field

from api.stack import (
    PackageIndex,
    StackParseError,
    analyze_dependency_file,
    build_index,
    display_name,
    unknown_tool_ids,
)
from ingest.tag import load_tools

TOOLS_PATH = Path(__file__).resolve().parent.parent / "data" / "tools.yaml"
MAX_PICKED_TOOLS = 100
PICKED = "picked"

router = APIRouter(prefix="/api", tags=["stack"])
tracer = trace.get_tracer("debrief")


class DependencyFile(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str


class StackRequest(BaseModel):
    tools: list[str] = Field(default_factory=list, max_length=MAX_PICKED_TOOLS)
    dependency_file: DependencyFile | None = None


class StackTool(BaseModel):
    id: str
    name: str
    source: str


class StackResponse(BaseModel):
    tools: list[StackTool]
    unknown_packages: list[str]


@lru_cache(maxsize=1)
def tool_registry() -> tuple[dict[str, str], PackageIndex]:
    """Tool names by id, and the package index, loaded once from tools.yaml."""
    tools = load_tools(os.environ.get("DEBRIEF_TOOLS") or TOOLS_PATH)
    return {t.id: t.name for t in tools}, build_index(tools)


@router.post("/stack")
def post_stack(request: StackRequest) -> StackResponse:
    names, index = tool_registry()
    missing = unknown_tool_ids(request.tools, index)
    if missing:
        raise HTTPException(status_code=422, detail=f"unknown tool ids: {', '.join(missing)}")

    tools: dict[str, StackTool] = {}
    for tool_id in request.tools:
        tools.setdefault(tool_id, StackTool(id=tool_id, name=names[tool_id], source=PICKED))

    unknown: list[str] = []
    if request.dependency_file is not None:
        file = request.dependency_file
        source = display_name(file.name)
        with tracer.start_as_current_span("stack.parse") as span:
            try:
                match = analyze_dependency_file(file.name, file.content, index)
            except StackParseError as exc:
                span.set_attribute("debrief.stack.error", True)
                raise HTTPException(status_code=422, detail=str(exc)) from None
            span.set_attribute("debrief.stack.file_type", match.file_type.value)
            span.set_attribute("debrief.stack.package_count", match.package_count)
            span.set_attribute("debrief.stack.matched_tool_count", len(match.tool_ids))
            span.set_attribute("debrief.stack.unknown_count", len(match.unknown))
            span.set_attribute("debrief.stack.tool_ids", match.tool_ids)
        for tool_id in match.tool_ids:
            tools.setdefault(tool_id, StackTool(id=tool_id, name=names[tool_id], source=source))
        unknown = match.unknown

    return StackResponse(tools=list(tools.values()), unknown_packages=unknown)
