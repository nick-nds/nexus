r"""``list_scheduled_tasks`` — enumerate Laravel's ``app/Console/Kernel`` schedule.

Each scheduled entry — ``$schedule->job(...)``, ``$schedule->command(...)``,
``$schedule->call(...)`` — produces one row in the response. Rows
include the cron expression, timezone, the dispatched class FQN
(when statically resolvable), and the ``without_overlapping`` /
``on_one_server`` modifiers an agent typically wants to confirm
when reasoning about idempotency or distributed-cron safety.

Why a dedicated tool instead of "look it up in describe_class"
==============================================================

Schedule entries don't live on a class. They're attached to the
``Console\Kernel`` instance via the ``->schedule()`` method, but
agents asking "what's scheduled?" don't want to traverse a kernel
class — they want a flat list. This tool gives them one.

The data is sourced from the ``scheduled_task`` graph nodes
populated by :meth:`nexus.core.graph.builder.GraphBuilder._build_schedule`
from the reflection's ``schedule`` section.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import bool_attr, str_attr
from nexus.core.query.traversal import outgoing

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class ListScheduledTasksInput(ToolInput):
    """Optional filters narrowing the schedule list.

    Filters compose with AND. Unset filters mean "any".
    """

    target_glob: str | None = Field(
        default=None,
        description=(
            "Shell-style glob matched against the resolved target FQN "
            "(e.g. ``App\\Jobs\\*``). Useful for finding 'what schedules "
            "anything in the SendEmail* family?'. Case-sensitive."
        ),
    )
    expression_glob: str | None = Field(
        default=None,
        description=(
            "Glob matched against the cron expression (e.g. "
            "``0 * * * *`` for hourly runs). Case-sensitive."
        ),
    )


class ScheduledTaskRow(ToolOutput):
    """One scheduled entry in the response."""

    id: str = Field(description="Stable graph id of the scheduled_task node.")
    expression: str = Field(description="The cron expression as written in the kernel.")
    timezone: str | None = None
    description: str | None = Field(
        default=None,
        description=(
            "The ``->name()`` / ``->description()`` annotation if the "
            "kernel set one; defaults to the target FQN."
        ),
    )
    kind: str = Field(
        description=(
            "Either ``\"command\"`` (``$schedule->command('cache:clear')``) "
            'or ``"callback"`` (``$schedule->job(new MyJob)`` or '
            "``$schedule->call(...)``)."
        ),
    )
    target: str | None = Field(
        default=None,
        description=(
            "Resolved target FQN when statically determinable — the job/"
            "command class. ``None`` for closure-based ``->call()`` "
            "entries we can't trace at extraction time."
        ),
    )
    command: str | None = Field(
        default=None,
        description=(
            "The Artisan signature passed to ``->command(...)``, e.g. "
            '``"cache:clear"``. ``None`` for callback-kind entries.'
        ),
    )
    without_overlapping: bool = False
    on_one_server: bool = False


class ListScheduledTasksOutput(ToolOutput):
    """Container for the scheduled-task list response."""

    total: int = Field(description="Total scheduled tasks before truncation.")
    returned: int = Field(description="Number of tasks actually in ``tasks``.")
    tasks: list[ScheduledTaskRow] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("tasks",)


class ListScheduledTasksTool:
    """Enumerate Laravel scheduled tasks.

    The tool reads ``scheduled_task`` nodes from the persisted graph
    and applies in-memory glob filters. Cost is O(scheduled tasks);
    a typical Laravel app has 5-50 entries so latency is well under
    the structural-tool budget.
    """

    name: ClassVar[str] = "list_scheduled_tasks"
    description: ClassVar[str] = (
        "List Laravel's scheduled tasks (``app/Console/Kernel`` schedule "
        "entries) with their cron expressions, target classes, and "
        "idempotency modifiers. Optionally filter by target FQN glob or "
        "cron-expression glob. The ``target`` field carries the resolved "
        "class for ``->job()`` / typed ``->call()`` entries; closures "
        "leave it ``None``. Pair with ``find_callers`` on a returned "
        "target to trace 'what code runs on this schedule?'."
    )
    input_model: ClassVar[type[ToolInput]] = ListScheduledTasksInput
    output_model: ClassVar[type[ToolOutput]] = ListScheduledTasksOutput
    latency_budget_ms: ClassVar[int] = 100

    def execute(
        self,
        payload: ListScheduledTasksInput,
        ctx: QueryContext,
    ) -> ListScheduledTasksOutput:
        """Return all scheduled tasks matching the filters."""
        graph = ctx.storage.graph().load()

        rows: list[ScheduledTaskRow] = []
        for node in graph.nodes:
            if node.kind != NodeKind.SCHEDULED_TASK:
                continue

            attrs = node.attributes
            target = _resolve_target(graph, node.id) or str_attr(attrs, "callback_target")
            command = str_attr(attrs, "command")
            expression = str_attr(attrs, "expression") or ""

            if payload.expression_glob and not fnmatch.fnmatchcase(
                expression,
                payload.expression_glob,
            ):
                continue
            if payload.target_glob:
                candidate = target or command or ""
                if not fnmatch.fnmatchcase(candidate, payload.target_glob):
                    continue

            rows.append(
                ScheduledTaskRow(
                    id=node.id,
                    expression=expression,
                    timezone=str_attr(attrs, "timezone"),
                    description=node.name,
                    kind=str_attr(attrs, "kind") or "callback",
                    target=target,
                    command=command,
                    without_overlapping=bool_attr(attrs, "without_overlapping"),
                    on_one_server=bool_attr(attrs, "on_one_server"),
                ),
            )

        rows.sort(key=lambda r: (r.expression, r.target or r.command or ""))

        return ListScheduledTasksOutput(
            total=len(rows),
            returned=len(rows),
            tasks=rows,
        )


def _resolve_target(graph: "Graph", scheduled_task_id: str) -> str | None:  # noqa: UP037
    """Walk RUNS_COMMAND outgoing edge to recover the target FQN.

    Falls back to the ``callback_target`` attribute when no edge
    exists (command-signature schedules, closures).  Returning the
    bare FQN — without a ``class:`` prefix — keeps the response
    consistent with the rest of the tool surface; agents shouldn't
    have to know about graph-internal id schemes.
    """
    from nexus.core.query.tools._common import fqn_from_class_id  # noqa: PLC0415

    for edge in outgoing(graph, scheduled_task_id, EdgeKind.RUNS_COMMAND):
        return fqn_from_class_id(graph, edge.target)
    return None


if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
