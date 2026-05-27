"""Unit tests for :class:`ListScheduledTasksTool`.

Builds a minimal in-memory graph with a couple of scheduled_task
nodes plus their RUNS_COMMAND edges, then asserts the tool returns
the expected rows under each filter.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.list_scheduled_tasks import (
    ListScheduledTasksInput,
    ListScheduledTasksTool,
)


def _make_graph_with_schedules() -> Graph:
    """A graph with two scheduled tasks: a callback job + a command-signature."""
    graph = Graph()

    # Callback-style schedule running a Job class.
    graph.add_node(
        Node(
            id="schedule:0 2 * * *|App\\Jobs\\NightlyCleanup",
            kind=NodeKind.SCHEDULED_TASK,
            name="Nightly cleanup",
            attributes={
                "expression": "0 2 * * *",
                "timezone": "UTC",
                "command": None,
                "callback_target": "App\\Jobs\\NightlyCleanup",
                "without_overlapping": True,
                "on_one_server": False,
                "kind": "callback",
            },
        ),
    )
    graph.add_node(
        Node(
            id="class:App\\Jobs\\NightlyCleanup",
            kind=NodeKind.JOB,
            name="App\\Jobs\\NightlyCleanup",
            attributes={"file": "/app/Jobs/NightlyCleanup.php"},
        ),
    )
    graph.add_edge(
        Edge(
            source="schedule:0 2 * * *|App\\Jobs\\NightlyCleanup",
            target="class:App\\Jobs\\NightlyCleanup",
            kind=EdgeKind.RUNS_COMMAND,
            attributes={"expression": "0 2 * * *"},
        ),
    )

    # Command-signature schedule (no class FQN, no edge).
    graph.add_node(
        Node(
            id="schedule:*/5 * * * *|cache:clear",
            kind=NodeKind.SCHEDULED_TASK,
            name="cache:clear",
            attributes={
                "expression": "*/5 * * * *",
                "timezone": "UTC",
                "command": "cache:clear",
                "callback_target": None,
                "without_overlapping": False,
                "on_one_server": True,
                "kind": "command",
            },
        ),
    )
    return graph


def _make_ctx(graph: Graph) -> QueryContext:
    """Build a QueryContext whose storage returns ``graph`` from ``graph().load()``."""
    graph_handle = MagicMock()
    graph_handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = graph_handle
    return QueryContext(
        storage=storage,
        budget=ResponseBudget(),
    )


def test_lists_all_scheduled_tasks_when_no_filter() -> None:
    ctx = _make_ctx(_make_graph_with_schedules())

    output = ListScheduledTasksTool().execute(ListScheduledTasksInput(), ctx)

    assert output.total == 2
    assert output.returned == 2
    expressions = {row.expression for row in output.tasks}
    assert expressions == {"0 2 * * *", "*/5 * * * *"}


def test_resolves_target_fqn_via_runs_command_edge() -> None:
    """The job target appears as a bare FQN, no ``class:`` prefix leak."""
    ctx = _make_ctx(_make_graph_with_schedules())

    output = ListScheduledTasksTool().execute(ListScheduledTasksInput(), ctx)

    nightly = next(r for r in output.tasks if r.expression == "0 2 * * *")
    assert nightly.target == "App\\Jobs\\NightlyCleanup"
    assert nightly.command is None
    assert nightly.kind == "callback"
    assert nightly.without_overlapping is True
    assert nightly.on_one_server is False


def test_command_signature_row_has_no_target_but_keeps_command() -> None:
    ctx = _make_ctx(_make_graph_with_schedules())

    output = ListScheduledTasksTool().execute(ListScheduledTasksInput(), ctx)

    cache = next(r for r in output.tasks if r.expression == "*/5 * * * *")
    assert cache.target is None
    assert cache.command == "cache:clear"
    assert cache.kind == "command"
    assert cache.on_one_server is True


def test_target_glob_filters_to_matching_class() -> None:
    ctx = _make_ctx(_make_graph_with_schedules())

    # ``App\Jobs\*`` as a literal - single backslashes in the pattern
    # since ``fnmatch`` doesn't treat ``\`` as an escape.
    output = ListScheduledTasksTool().execute(
        ListScheduledTasksInput(target_glob=r"App\Jobs\*"),
        ctx,
    )

    assert output.total == 1
    assert output.tasks[0].target == "App\\Jobs\\NightlyCleanup"


def test_expression_glob_filter() -> None:
    ctx = _make_ctx(_make_graph_with_schedules())

    output = ListScheduledTasksTool().execute(
        ListScheduledTasksInput(expression_glob="*/5*"),
        ctx,
    )

    assert output.total == 1
    assert output.tasks[0].command == "cache:clear"


def test_filters_combine_with_and_logic() -> None:
    """A glob that matches expression but not target excludes the row."""
    ctx = _make_ctx(_make_graph_with_schedules())

    output = ListScheduledTasksTool().execute(
        ListScheduledTasksInput(
            expression_glob="0 2 * * *",
            target_glob=r"App\NotMatching\*",
        ),
        ctx,
    )

    assert output.total == 0
    assert output.tasks == []


def test_returns_empty_when_no_scheduled_tasks_in_graph() -> None:
    """Project without a schedule returns a clean empty response, not an error."""
    graph = Graph()
    ctx = _make_ctx(graph)

    output = ListScheduledTasksTool().execute(ListScheduledTasksInput(), ctx)

    assert output.total == 0
    assert output.returned == 0
    assert output.tasks == []
    assert output.error_code is None
