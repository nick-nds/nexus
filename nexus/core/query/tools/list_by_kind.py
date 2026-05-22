r"""``list_by_kind`` — enumerate every class node of a given kind.

A generic counterpart to :class:`ListRoutesTool` and
:class:`ListScheduledTasksTool` for the kinds that don't have (and
shouldn't get) their own dedicated list tool: events, jobs,
notifications, listeners, observers, mailables, models, controllers,
form requests, policies, commands, resources, casts, service
providers, and the catch-all ``class`` kind.

Why a single generic tool
=========================

15 dedicated ``list_events`` / ``list_jobs`` / ``list_X`` tools is
register-spam that bloats the MCP tool list and pushes useful tools
off the agent's radar. Each of those would have the same shape: a
filter against ``class:`` nodes of a given kind. One generic tool
with a kind argument keeps the surface tight while letting an agent
ask "what events exist" or "show me all jobs matching ``*Webhook*``"
with the same primitive.

Filter shape
============

* ``kind`` — required, validated against the closed set the graph
  builder produces. Misspellings get rejected with a clear error
  message listing the valid values.
* ``name_glob`` — optional shell-style glob applied to the class
  short name (case-sensitive, matching Laravel's class-naming
  conventions).
* ``namespace_prefix`` — optional FQN prefix (``"App\\Modules\\CRM"``)
  that scopes the response to one module of a DDD-style codebase.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


# Kinds the generic ``list_by_kind`` will enumerate. Routes and
# scheduled tasks are excluded — they have dedicated tools whose
# responses carry richer per-kind metadata (route URI + middleware
# list, cron expression + target, etc.) that this generic tool can't
# reasonably surface.
#
# Middleware is included because user-authored middleware *classes*
# carry ``class:<fqn>`` ids that this tool already filters on (line
# below). Framework-level middleware *aliases* like ``middleware:auth``
# have non-``class:`` ids and are skipped naturally.
_LISTABLE_KINDS: frozenset[NodeKind] = frozenset(
    {
        NodeKind.CONTROLLER,
        NodeKind.MODEL,
        NodeKind.EVENT,
        NodeKind.LISTENER,
        NodeKind.JOB,
        NodeKind.NOTIFICATION,
        NodeKind.MAILABLE,
        NodeKind.POLICY,
        NodeKind.FORM_REQUEST,
        NodeKind.OBSERVER,
        NodeKind.RESOURCE,
        NodeKind.COMMAND,
        NodeKind.SERVICE_PROVIDER,
        NodeKind.CAST,
        NodeKind.MIDDLEWARE,
        NodeKind.CLASS,
    },
)


class ListByKindInput(ToolInput):
    """Filter parameters for the kind-scoped enumeration."""

    kind: str = Field(
        description=(
            "NodeKind to enumerate: ``event``, ``job``, ``notification``, "
            "``listener``, ``model``, ``controller``, ``form_request``, "
            "``policy``, ``observer``, ``mailable``, ``resource``, "
            "``command``, ``service_provider``, ``cast``, ``middleware``, "
            "or ``class``. Routes and scheduled tasks have dedicated "
            "tools (``list_routes`` / ``list_scheduled_tasks``)."
        ),
    )
    name_glob: str | None = Field(
        default=None,
        description=(
            "Shell-style glob matched against the class' short name "
            "(``*Webhook*``). Case-sensitive."
        ),
    )
    namespace_prefix: str | None = Field(
        default=None,
        description=(
            "Restrict to a module by FQN prefix, e.g. "
            "``App\\Modules\\CRM`` or ``App\\Http\\Controllers``. "
            "Useful on DDD codebases where one kind spans many modules."
        ),
    )


class KindRow(ToolOutput):
    """One class in the listing."""

    fqn: str
    short_name: str
    file: str | None = None
    namespace: str | None = None


class ListByKindOutput(ToolOutput):
    """Container for the enumeration response."""

    kind: str | None = None
    total: int = 0
    returned: int = 0
    items: list[KindRow] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("items",)


class ListByKindTool:
    """Enumerate class nodes of a given kind, optionally filtered by glob/prefix."""

    name: ClassVar[str] = "list_by_kind"
    description: ClassVar[str] = (
        "List every class of a given kind in the project — events, "
        "jobs, notifications, models, controllers, form requests, "
        "policies, observers, listeners, mailables, resources, "
        "commands, casts, service providers, middleware, or generic "
        "classes. "
        "**Argument:** ``kind`` (string) — one of the kinds listed "
        'above, e.g. ``kind="event"``, ``kind="middleware"``. '
        "**Optional:** ``name_glob`` (shell glob, e.g. "
        '``name_glob="*Webhook*"``) and ``namespace_prefix`` '
        '(e.g. ``namespace_prefix="App\\\\Modules\\\\CRM"``) narrow the '
        "result. Use as a generic discovery primitive when the agent "
        "wants 'all events' or 'all controllers under "
        "App\\\\Modules\\\\CRM'. Routes have ``list_routes``; scheduled "
        "tasks have ``list_scheduled_tasks``; for fuzzy short-name "
        "lookup across kinds use ``explore_entity``."
    )
    input_model: ClassVar[type[ToolInput]] = ListByKindInput
    output_model: ClassVar[type[ToolOutput]] = ListByKindOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: ListByKindInput,
        ctx: QueryContext,
    ) -> ListByKindOutput:
        """Return every class node matching ``payload.kind`` and the filters."""
        kind_value = payload.kind.strip().lower()
        try:
            target_kind = NodeKind(kind_value)
        except ValueError:
            return ListByKindOutput(
                kind=payload.kind,
                error=(
                    f"Unknown kind {payload.kind!r}. Valid values: "
                    + ", ".join(sorted(k.value for k in _LISTABLE_KINDS))
                ),
                error_code="invalid_kind",
            )

        if target_kind not in _LISTABLE_KINDS:
            return ListByKindOutput(
                kind=payload.kind,
                error=(
                    f"Kind {payload.kind!r} has a dedicated tool — try "
                    f"``list_routes``, ``list_scheduled_tasks``, or "
                    f"``explore_entity`` instead."
                ),
                error_code="non_listable_kind",
            )

        graph = ctx.storage.graph().load()
        rows: list[KindRow] = []
        for node in graph.nodes:
            if node.kind != target_kind:
                continue
            if not node.id.startswith("class:"):
                continue
            fqn = node.id[len("class:") :]
            if payload.namespace_prefix and not fqn.startswith(payload.namespace_prefix):
                continue
            if payload.name_glob and not fnmatch.fnmatchcase(node.name, payload.name_glob):
                continue
            rows.append(
                KindRow(
                    fqn=fqn,
                    short_name=node.name,
                    file=str_attr(node.attributes, "file"),
                    namespace=str_attr(node.attributes, "namespace"),
                ),
            )

        rows.sort(key=lambda r: r.fqn)

        return ListByKindOutput(
            kind=target_kind.value,
            total=len(rows),
            returned=len(rows),
            items=rows,
        )
