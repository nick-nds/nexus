"""Sequential pipeline orchestrator.

``Pipeline.run`` takes a list of passes plus a built context and runs
every pass in order, emitting progress events and short-circuiting on
the first error. There is no DAG and no parallelism; a linear sequence
is easier to reason about and recover from, and every pass listed in
``PHASE-3-indexing-pipeline.md`` is linear anyway.

The orchestrator catches unexpected exceptions from passes — a
pass-level bug should not take down the whole process without leaving
a trace. Caught exceptions become an :class:`Error` on the context
with a ``pass_crashed`` code, and the pipeline result captures the
offending pass name.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nexus.core.outcome import Error
from nexus.pipeline.progress import (
    PassFinished,
    PassStarted,
    PipelineFinished,
)

if TYPE_CHECKING:
    from nexus.pipeline.context import PipelineContext
    from nexus.pipeline.pass_protocol import Pass


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Summary returned by :meth:`Pipeline.run`.

    Attributes:
        ok: ``True`` when every pass completed without an error.
        passes_run: Names of passes that reached completion.
        pass_durations_ms: Per-pass wall-clock time.
        crashed_pass: Name of the first pass that raised an
            unexpected exception, or ``None`` if no crash occurred.
    """

    ok: bool
    passes_run: tuple[str, ...] = ()
    pass_durations_ms: dict[str, float] = field(default_factory=dict)
    crashed_pass: str | None = None


class Pipeline:
    """Runs an ordered list of :class:`Pass` instances against a context."""

    def __init__(self, passes: list[Pass]) -> None:
        """Build a pipeline from a list of passes.

        Args:
            passes: Ordered list of pass instances. The orchestrator
                runs them in list order and stops at the first error.
        """
        self._passes = list(passes)

    @property
    def passes(self) -> list[Pass]:
        """Return the ordered list of passes, for introspection."""
        return list(self._passes)

    def run(self, ctx: PipelineContext) -> PipelineResult:
        """Execute every pass against ``ctx``.

        Args:
            ctx: A :class:`PipelineContext` already populated with the
                project path, storage, profile, and (optionally) the
                embedder.

        Returns:
            A :class:`PipelineResult` summarising what happened. The
            context itself carries the full warnings and errors.
        """
        start = time.perf_counter()
        completed: list[str] = []
        durations: dict[str, float] = {}
        crashed_pass: str | None = None

        for pass_ in self._passes:
            if not ctx.ok():
                break

            ctx.progress.emit(PassStarted(pass_name=pass_.name))
            pass_start = time.perf_counter()
            crashed = False

            try:
                pass_.run(ctx)
            except Exception as exc:
                ctx.add_error(
                    Error(
                        code="pass_crashed",
                        message=f"{pass_.name} raised {type(exc).__name__}: {exc}",
                        context={
                            "pass": pass_.name,
                            "traceback": traceback.format_exc(),
                        },
                    ),
                )
                crashed_pass = pass_.name
                crashed = True

            duration = (time.perf_counter() - pass_start) * 1000.0
            durations[pass_.name] = duration
            completed.append(pass_.name)

            ctx.progress.emit(
                PassFinished(
                    pass_name=pass_.name,
                    ok=not crashed and ctx.ok(),
                    duration_ms=duration,
                    warnings=len(ctx.warnings),
                    errors=len(ctx.errors),
                ),
            )

            if crashed:
                break

        total_ms = (time.perf_counter() - start) * 1000.0

        ctx.progress.emit(
            PipelineFinished(
                ok=ctx.ok(),
                duration_ms=total_ms,
                passes_completed=len(completed),
                passes_skipped=len(self._passes) - len(completed),
            ),
        )

        return PipelineResult(
            ok=ctx.ok(),
            passes_run=tuple(completed),
            pass_durations_ms=durations,
            crashed_pass=crashed_pass,
        )
