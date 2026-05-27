"""``nexus trace`` - inspect query trace files.

Trace files are written by ``nexus ask --trace <path>`` and by the
MCP server when ``NEXUS_TRACE_DIR`` is set. They're append-only
JSONL with a stable schema (see ``nexus.core.query.trace``).

The ``inspect`` subcommand reads one trace and emits a compact
summary: the classifier decision, every tool call with duration and
outcome, and the final envelope/refusal. The output is the same
shape under either ``--format json`` or ``--format pretty`` - for
raw records, pipe the trace file through ``jq`` directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


@click.group(name="trace", help="Inspect query trace files.")
def trace_group() -> None:
    """Trace-related subcommands."""


@trace_group.command(name="inspect", help="Pretty-print a trace file.")
@click.argument(
    "path",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--last",
    is_flag=True,
    default=False,
    help=(
        "Inspect the most recent trace under <storage-root>/traces/. Mutually exclusive with PATH."
    ),
)
@click.pass_obj
def inspect_command(
    cli_ctx: CliContext,
    path: Path | None,
    last: bool,
) -> None:
    """Read PATH (or the most recent trace) and print a structured summary."""
    if path is not None and last:
        print_error(cli_ctx, "PATH and --last are mutually exclusive")
        raise click.exceptions.Exit(2)

    if last:
        resolved = _find_latest_trace(cli_ctx.storage_root)
        if resolved is None:
            print_error(
                cli_ctx,
                f"no trace files under {cli_ctx.storage_root / 'traces'}",
                hint="run `nexus ask --trace <path>` first",
            )
            raise click.exceptions.Exit(1)
        path = resolved

    if path is None:
        print_error(cli_ctx, "either PATH or --last is required")
        raise click.exceptions.Exit(2)

    if not path.exists():
        print_error(cli_ctx, f"trace file not found: {path}")
        raise click.exceptions.Exit(1)

    records = _read_records(path)
    if not records:
        print_error(cli_ctx, f"trace file is empty: {path}")
        raise click.exceptions.Exit(1)

    summary = _summarise(path, records)
    render(cli_ctx, summary)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_latest_trace(storage_root: Path) -> Path | None:
    """Return the most recent ``.jsonl`` under ``<storage_root>/traces/``."""
    traces_root = storage_root / "traces"
    if not traces_root.exists():
        return None
    candidates = list(traces_root.rglob("*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL trace file. Skips malformed lines silently."""
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return records


def _summarise(path: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact, human-shaped view of one trace."""
    first_ts = records[0].get("ts")
    trace_id = records[0].get("trace_id")
    query: str | None = None
    classifier: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = []
    outcome: dict[str, Any] | None = None

    for rec in records:
        kind = rec.get("kind")
        if kind == "classifier_decision":
            query = rec.get("query")
            classifier = rec.get("plan")
        elif kind == "tool_executed":
            tool_calls.append(
                {
                    "tool": rec.get("tool"),
                    "duration_ms": rec.get("duration_ms"),
                    "error_code": rec.get("error_code"),
                    "result_size": rec.get("result_size"),
                    "over_budget": rec.get("over_budget"),
                },
            )
        elif kind in {"ask_envelope", "ask_refusal"}:
            outcome = {"kind": kind, **{k: v for k, v in rec.items() if k != "kind"}}

    total_ms = sum((c.get("duration_ms") or 0.0) for c in tool_calls)

    return {
        "path": str(path),
        "trace_id": trace_id,
        "started_at": first_ts,
        "query": query,
        "classifier": classifier,
        "tool_calls": tool_calls,
        "tool_calls_count": len(tool_calls),
        "total_tool_duration_ms": round(total_ms, 2),
        "outcome": outcome,
    }
