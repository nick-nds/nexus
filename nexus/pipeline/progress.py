"""Structured progress events and a default reporter.

The pipeline emits typed events to a :class:`ProgressReporter`. The
reporter is an abstract protocol; concrete implementations in Phase 5
render them to a TTY with :mod:`rich` progress bars or emit them as
JSON lines for CI tails. Keeping the event types structured means the
pipeline never has to know whether it is talking to a human or a log
collector — it just calls ``reporter.emit(event)``.

Design notes
============

* Events are frozen dataclasses so consumers can compare them by value
  in tests.
* The event taxonomy is deliberately small: four types cover every
  observable state transition we need for the Phase 3 passes. A
  richer event set can be added without breaking consumers because
  they match on ``type``.
* The default :class:`LoggingProgressReporter` writes to structlog so
  library users get useful output without pulling in any UI code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from nexus.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class PassStarted:
    """Emitted once when a pass begins running."""

    pass_name: str


@dataclass(frozen=True, slots=True)
class PassProgress:
    """Emitted zero or more times during a pass for in-progress updates.

    Attributes:
        pass_name: The name of the pass emitting the update.
        message: Short human-readable description of what's happening.
        current: Optional integer progress counter (current step).
        total: Optional total for the counter (total steps).
        detail: Optional structured payload for machine consumption.
    """

    pass_name: str
    message: str
    current: int | None = None
    total: int | None = None
    detail: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class PassFinished:
    """Emitted once per pass when it finishes (success or failure)."""

    pass_name: str
    ok: bool
    duration_ms: float
    warnings: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class PipelineFinished:
    """Emitted once when the orchestrator has run every pass."""

    ok: bool
    duration_ms: float
    passes_completed: int
    passes_skipped: int = 0


ProgressEvent = PassStarted | PassProgress | PassFinished | PipelineFinished


class ProgressReporter(Protocol):
    """Pluggable sink for :class:`ProgressEvent` instances.

    Implementations must be robust to being called from any pass
    implementation — a thrown exception in a reporter would cascade
    into the orchestrator, so reporters should swallow their own
    problems.
    """

    def emit(self, event: ProgressEvent) -> None:
        """Record or render a single progress event."""
        ...


class NullProgressReporter:
    """A reporter that discards every event.

    Useful as the default when no caller has subscribed — the pipeline
    should never fail because no one is listening.
    """

    def emit(self, event: ProgressEvent) -> None:
        """Discard the event."""
        return


class LoggingProgressReporter:
    """Writes progress events to the structlog logger.

    Concrete reporter used by library users who haven't set up a
    richer UI. Phase 5's CLI swaps in a :mod:`rich`-backed
    implementation for interactive runs.
    """

    def __init__(self, logger_name: str = "nexus.pipeline") -> None:
        self._log = get_logger(logger_name)

    def emit(self, event: ProgressEvent) -> None:
        """Log one event at an appropriate level."""
        match event:
            case PassStarted(pass_name=name):
                self._log.info("pass_started", pass_name=name)
            case PassProgress() as e:
                self._log.info(
                    "pass_progress",
                    pass_name=e.pass_name,
                    message=e.message,
                    current=e.current,
                    total=e.total,
                    detail=e.detail,
                )
            case PassFinished() as e:
                level = self._log.info if e.ok else self._log.error
                level(
                    "pass_finished",
                    pass_name=e.pass_name,
                    ok=e.ok,
                    duration_ms=round(e.duration_ms, 2),
                    warnings=e.warnings,
                    errors=e.errors,
                )
            case PipelineFinished() as e:
                level = self._log.info if e.ok else self._log.error
                level(
                    "pipeline_finished",
                    ok=e.ok,
                    duration_ms=round(e.duration_ms, 2),
                    passes_completed=e.passes_completed,
                    passes_skipped=e.passes_skipped,
                )


class CollectingProgressReporter:
    """Testing helper that accumulates every event for later assertion.

    Not part of the public API of the pipeline but exported here so
    tests across subpackages can import it without redefining it.
    """

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        """Append the event to the internal list."""
        self.events.append(event)

    def events_of_type(self, type_: type) -> Iterable[ProgressEvent]:
        """Return only events of the given dataclass type."""
        return (e for e in self.events if isinstance(e, type_))
