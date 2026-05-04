"""Second pass: turn the loaded reflection into a typed :class:`Graph`.

Thin wrapper around :class:`~nexus.core.graph.builder.GraphBuilder`.
The pass surfaces the builder's warnings onto the context and stops
the pipeline with a typed error if the reflection wasn't loaded by
the preceding :class:`RunExtractorPass`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.core.graph.builder import GraphBuilder
from nexus.core.outcome import Error
from nexus.pipeline.progress import PassProgress

if TYPE_CHECKING:
    from nexus.pipeline.context import PipelineContext


class BuildGraphPass:
    """Run the graph builder against the context's reflection document."""

    name = "build_graph"

    def __init__(self, builder: GraphBuilder | None = None) -> None:
        """Build the pass with an optional builder override (tests)."""
        self._builder = builder or GraphBuilder()

    def run(self, ctx: PipelineContext) -> None:
        """Invoke the builder and stash the graph on the context."""
        if ctx.reflection is None:
            ctx.add_error(
                Error(
                    code="no_reflection",
                    message=(
                        "BuildGraphPass needs a reflection document. "
                        "Did RunExtractorPass run successfully?"
                    ),
                ),
            )
            return

        result = self._builder.build(ctx.reflection, ctx.profile)

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=(f"Built {len(result.value.nodes)} nodes, {len(result.value.edges)} edges"),
                detail={
                    "nodes": len(result.value.nodes),
                    "edges": len(result.value.edges),
                    "warnings": len(result.warnings),
                },
            ),
        )

        ctx.graph = result.value
        ctx.warnings.extend(result.warnings)
