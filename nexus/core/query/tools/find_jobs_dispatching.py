"""``find_jobs_dispatching`` — find places that dispatch a given job.

Mirror of :mod:`find_dispatchers` but for jobs. Walks ``DISPATCHES``
edges backwards from the job node. Useful for questions like
*"where is ``ProcessPayment`` actually queued from?"*.

In v1 the static analyser populates ``DISPATCHES`` edges from
direct ``::dispatch(...)`` calls. Indirect dispatches via a
custom job bus are not yet tracked and will appear as empty
results; that's a known limitation documented in Phase 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import int_attr, str_attr
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.query.context import QueryContext


class FindJobsDispatchingInput(ToolInput):
    """Identify the job class to search for."""

    job: str = Field(
        description="Job FQN or ``job:<fqn>`` graph id.",
    )


class DispatchSite(ToolOutput):
    """One place the job is dispatched from."""

    class_fqn: str | None = None
    method: str
    file: str | None = None
    line: int | None = None


class FindJobsDispatchingOutput(ToolOutput):
    """Container for dispatch-site rows."""

    job: str | None = None
    total: int = 0
    returned: int = 0
    sites: list[DispatchSite] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("sites",)


class FindJobsDispatchingTool:
    """Find every method that dispatches a specific job."""

    name: ClassVar[str] = "find_jobs_dispatching"
    description: ClassVar[str] = (
        "Given a job FQN, return every method that dispatches it via "
        "Laravel's queue. "
        "**Argument:** ``job`` (string) — the job's FQN, e.g. "
        '``job="App\\\\Jobs\\\\SendInvoiceEmail"``. '
        "Each site points at the caller's class, method, file, and line."
    )
    input_model: ClassVar[type[ToolInput]] = FindJobsDispatchingInput
    output_model: ClassVar[type[ToolOutput]] = FindJobsDispatchingOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: FindJobsDispatchingInput,
        ctx: QueryContext,
    ) -> FindJobsDispatchingOutput:
        """Walk ``DISPATCHES`` edges backwards from the job node."""
        graph = ctx.storage.graph().load()

        job_id = _resolve_job_id(graph, payload.job)
        if job_id is None:
            return FindJobsDispatchingOutput(
                job=payload.job,
                error=f"No job found matching {payload.job!r}.",
                error_code="job_not_found",
            )

        sites: list[DispatchSite] = []
        for edge in incoming(graph, job_id, EdgeKind.DISPATCHES):
            method_node = graph.node_by_id(edge.source)
            if method_node is None:
                continue
            attrs = method_node.attributes
            sites.append(
                DispatchSite(
                    class_fqn=str_attr(attrs, "class_fqn"),
                    method=method_node.name,
                    file=str_attr(attrs, "file"),
                    line=int_attr(attrs, "line"),
                ),
            )

        sites.sort(key=lambda s: (s.class_fqn or "", s.method))

        return FindJobsDispatchingOutput(
            job=job_id,
            total=len(sites),
            returned=len(sites),
            sites=sites,
        )


def _resolve_job_id(graph: Graph, query: str) -> str | None:
    if query.startswith("job:"):
        return query if graph.node_by_id(query) is not None else None
    candidate = f"job:{query}"
    if graph.node_by_id(candidate) is not None:
        return candidate
    for node in graph.nodes:
        if node.kind == NodeKind.JOB and node.name == query:
            return node.id
    return None
