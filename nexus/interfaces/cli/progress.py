"""Rich-backed progress reporter for the CLI.

Subscribes to the pipeline's :class:`ProgressReporter` protocol and
renders events as a live rich :class:`Progress` display with one task
per pipeline pass. A non-TTY fallback emits structured JSON lines
suitable for CI logs; the auto-detection lives on the
:class:`CliContext` so every command honours the same policy.

Design notes
============

* We deliberately keep one :class:`rich.progress.Progress` instance
  for the whole pipeline run rather than a nested progress tree —
  each pass is a numbered task with a short description and
  optional numeric progress. A nested tree looks fancier but is
  hard to tail on a small terminal.
* The reporter swallows rendering exceptions to honour the
  :class:`ProgressReporter` contract ("reporters should swallow
  their own problems"). A broken progress bar must not crash the
  pipeline.
* For JSON-lines mode we emit one line per event to stdout so
  ``nexus index rebuild > log.ndjson`` works verbatim. Each line
  is a flat dict keyed by ``event`` type.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, TextIO

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from nexus.logging import get_logger
from nexus.pipeline.progress import (
    PassFinished,
    PassProgress,
    PassStarted,
    PipelineFinished,
)

log = get_logger(__name__)

if TYPE_CHECKING:
    from nexus.pipeline.progress import ProgressEvent


class RichProgressReporter:
    """Render pipeline events as a live rich progress display.

    Usage::

        with RichProgressReporter() as reporter:
            ctx.progress = reporter
            pipeline.run(ctx)

    The context-manager form is required so the rich :class:`Progress`
    can stop and clean up the terminal on exit. Using the reporter
    outside a ``with`` block will still accept events but no live
    display is shown.
    """

    def __init__(self, *, console: Console | None = None) -> None:
        self._console = console or Console(file=sys.stderr)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("{task.fields[message]}"),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
        )
        self._task_ids: dict[str, int] = {}
        self._active = False

    # ------------------------------------------------------------------
    # Context-manager lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> RichProgressReporter:
        """Start the rich live display."""
        self._progress.start()
        self._active = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Stop the rich live display."""
        if self._active:
            self._progress.stop()
            self._active = False

    # ------------------------------------------------------------------
    # ProgressReporter implementation
    # ------------------------------------------------------------------

    def emit(self, event: ProgressEvent) -> None:
        """Route one pipeline event into a task row update.

        Rendering errors are swallowed (per the
        :class:`ProgressReporter` contract — a broken progress bar
        must not crash the pipeline) but logged at warning level so
        a misbehaving terminal renderer leaves a structured-log
        breadcrumb instead of vanishing without trace.
        """
        try:
            self._emit(event)
        except Exception as exc:
            log.warning(
                "progress_emit_failed",
                reporter="rich",
                event_type=type(event).__name__,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return

    def _emit(self, event: ProgressEvent) -> None:
        match event:
            case PassStarted(pass_name=name):
                self._task_ids[name] = self._progress.add_task(
                    description=name,
                    total=None,
                    message="",
                )
            case PassProgress() as e:
                tid = self._task_ids.get(e.pass_name)
                if tid is None:
                    return
                update_kw: dict[str, object] = {}
                if e.total is not None:
                    update_kw["total"] = e.total
                if e.current is not None:
                    update_kw["completed"] = e.current
                self._progress.update(tid, message=e.message, **update_kw)  # type: ignore[arg-type]
            case PassFinished() as e:
                tid = self._task_ids.get(e.pass_name)
                if tid is None:
                    return
                suffix = "done" if e.ok else "failed"
                task_total = self._progress.tasks[tid].total or 1
                self._progress.update(
                    tid,  # type: ignore[arg-type]
                    message=f"{suffix} in {e.duration_ms:.0f} ms",
                    completed=task_total,
                    total=task_total,
                )
            case PipelineFinished() as e:
                summary = "ok" if e.ok else "failed"
                self._console.print(
                    f"[bold]pipeline {summary}[/bold] "
                    f"in {e.duration_ms / 1000:.2f}s "
                    f"({e.passes_completed} pass(es))",
                )


class JsonLinesProgressReporter:
    """Non-TTY fallback that prints one JSON line per event.

    Used when ``nexus index rebuild`` is piped to a file or run under
    CI — no curses, no carriage returns, one line per event so the
    output is trivially parseable. Writes to stderr so stdout stays
    reserved for the final result payload.
    """

    def __init__(self, *, file: TextIO | None = None) -> None:
        self._file: TextIO = file or sys.stderr

    def emit(self, event: ProgressEvent) -> None:
        """Serialise one pipeline event to a single JSON line.

        Same swallow-but-log discipline as :class:`RichProgressReporter`:
        a broken stream or unprintable payload mustn't crash indexing,
        but the failure leaves a structured-log breadcrumb.
        """
        try:
            self._emit(event)
        except Exception as exc:
            log.warning(
                "progress_emit_failed",
                reporter="jsonl",
                event_type=type(event).__name__,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return

    def _emit(self, event: ProgressEvent) -> None:
        payload: dict[str, object] = {"event": type(event).__name__}
        match event:
            case PassStarted(pass_name=name):
                payload["pass"] = name
            case PassProgress() as e:
                payload["pass"] = e.pass_name
                payload["message"] = e.message
                if e.current is not None:
                    payload["current"] = e.current
                if e.total is not None:
                    payload["total"] = e.total
            case PassFinished() as e:
                payload["pass"] = e.pass_name
                payload["ok"] = e.ok
                payload["duration_ms"] = round(e.duration_ms, 2)
                payload["warnings"] = e.warnings
                payload["errors"] = e.errors
            case PipelineFinished() as e:
                payload["ok"] = e.ok
                payload["duration_ms"] = round(e.duration_ms, 2)
                payload["passes_completed"] = e.passes_completed
        line = json.dumps(payload)
        print(line, file=self._file, flush=True)
