"""Write debrief-health.dashboard.json (for Dash0's "Edit as JSON") from the YAML source.

Run: uv run python observability/dash0/to_json.py
"""

import json
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "debrief-health.dashboard.yaml"
TARGET = HERE / "debrief-health.dashboard.json"


def dashboard_json() -> dict[str, Any]:
    """The Perses JSON form: kind Dashboard, the CR's name, the same spec."""
    cr = yaml.safe_load(SOURCE.read_text())
    return {"kind": "Dashboard", "metadata": {"name": cr["metadata"]["name"]}, "spec": cr["spec"]}


if __name__ == "__main__":
    TARGET.write_text(json.dumps(dashboard_json(), indent=2) + "\n")
    print(f"wrote {TARGET.name}")
