"""``--max-items`` sets the response list cap for scripted callers.

Agents want the default 100-row cap to protect their context; scripts
want every row. ``0`` disables the cap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from nexus.adapters.storage import ProjectMeta
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.interfaces.cli.context import CliContext
from nexus.interfaces.cli.main import main

_CALLER_COUNT = 150
_TARGET = "App\\Money::toFloat"


def _method(fqn: str) -> Node:
    class_fqn, name = fqn.split("::")
    return Node(
        id=f"method:{fqn}",
        kind=NodeKind.METHOD,
        name=name,
        attributes={"class_fqn": class_fqn, "file": "/app.php", "line": 1},
    )


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    graph = Graph()
    graph.add_node(_method(_TARGET))
    for i in range(_CALLER_COUNT):
        caller = _method(f"App\\Caller{i:03d}::run")
        graph.add_node(caller)
        graph.add_edge(
            Edge(
                source=caller.id,
                target=f"method:{_TARGET}",
                kind=EdgeKind.CALLS,
                attributes={"file": "/caller.php", "line": i},
            ),
        )
    storage = CliContext(storage_root=tmp_path, project_slug="demo").storage()
    storage.initialise()
    storage.graph().persist(graph)
    storage.write_meta(
        ProjectMeta(project_slug="demo", project_path=str(tmp_path), lsp_server="intelephense"),
    )
    return tmp_path


def _find_callers(storage_root: Path, *global_args: str) -> dict[str, object]:
    result = CliRunner().invoke(
        main,
        [
            "--storage-root",
            str(storage_root),
            "--slug",
            "demo",
            "--format",
            "json",
            *global_args,
            "query",
            "find_callers",
            "--method-fqn",
            _TARGET,
        ],
    )
    assert result.exit_code == 0, result.output
    payload: dict[str, object] = json.loads(result.stdout)
    return payload


def test_default_caps_lists_at_100(storage_root: Path) -> None:
    payload = _find_callers(storage_root)

    assert len(payload["callers"]) == 100  # type: ignore[arg-type]
    assert payload["truncated"] is True


def test_max_items_sets_the_cap(storage_root: Path) -> None:
    payload = _find_callers(storage_root, "--max-items", "120")

    assert len(payload["callers"]) == 120  # type: ignore[arg-type]


def test_zero_max_items_returns_every_row(storage_root: Path) -> None:
    payload = _find_callers(storage_root, "--max-items", "0")

    assert len(payload["callers"]) == _CALLER_COUNT  # type: ignore[arg-type]
    assert payload["truncated"] is False


def test_negative_max_items_is_a_usage_error(storage_root: Path) -> None:
    result = CliRunner().invoke(
        main, ["--storage-root", str(storage_root), "--max-items", "-1", "query", "--help"]
    )

    assert result.exit_code == 2
