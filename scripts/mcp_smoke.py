#!/usr/bin/env python3
"""End-to-end MCP smoke test against a real index.

Builds the FastMCP server in-process against ``--slug`` (default
``synthesq-api``), discovers a representative target for each tool
that needs a parametrised arg (a real route URI, a real model FQN,
a real event, etc.), then invokes every registered tool through
the MCP ``call_tool`` API and prints a perf table.

Reports per tool:

* ``status`` — ``ok`` / ``error`` / ``over_budget`` / ``skipped``
* ``duration_ms`` vs ``budget_ms``
* ``error_code`` (if any) and ``result_size`` (length of the
  primary list field)

Run with:

    uv run python scripts/mcp_smoke.py
    uv run python scripts/mcp_smoke.py --slug my-other-project
    NEXUS_TRACE_DIR=/tmp/mcp-trace uv run python scripts/mcp_smoke.py

Exit code is non-zero when any tool errored unexpectedly or busted
its latency budget — convenient for CI gating.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.adapters.embedders.registration import register_builtin_embedders
from nexus.adapters.storage import ProjectStorage
from nexus.config.global_config import load_global_config
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.coverage import Coverage
from nexus.core.query.tools import register_builtin_tools
from nexus.interfaces.mcp import build_mcp_server
from nexus.plugins.registry import PluginRegistry


# ---------------------------------------------------------------------------
# Engine bootstrap
# ---------------------------------------------------------------------------


def build_engine(storage_root: Path, slug: str) -> QueryEngine:
    """Construct a real engine + context against the persisted index."""
    storage = ProjectStorage(root=storage_root, slug=slug)
    if not storage.project_dir.exists():
        msg = f"index not found at {storage.project_dir} — run `nexus index rebuild` first"
        raise SystemExit(msg)

    embedder = _resolve_embedder(storage_root)
    coverage = Coverage.from_meta(storage.read_meta())
    ctx = QueryContext(
        storage=storage,
        budget=ResponseBudget(),
        embedder=embedder,
        vector_dimensions=embedder.dimensions if embedder is not None else None,
        coverage=coverage,
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return QueryEngine(registry, ctx)


def _resolve_embedder(storage_root: Path) -> Any:
    """Build the configured embedder, or return None for graph-only mode."""
    cfg_path = storage_root / "config.yml"
    if not cfg_path.exists():
        return None
    cfg = load_global_config(cfg_path)
    plugins = PluginRegistry()
    register_builtin_embedders(plugins)
    config_dict: dict[str, object] = {"model": cfg.embedder.model}
    if cfg.embedder.dimensions is not None:
        config_dict["dimensions"] = cfg.embedder.dimensions
    try:
        return plugins.resolve_embedder(cfg.embedder.provider, config_dict)
    except Exception:  # noqa: BLE001 — graph-only fallback is acceptable
        return None


# ---------------------------------------------------------------------------
# Target discovery
# ---------------------------------------------------------------------------


@dataclass
class Targets:
    """Real values discovered from the index, plugged into parametrised tools."""

    route_uri: str | None = None
    route_method: str | None = None
    model_fqn: str | None = None
    class_fqn: str | None = None
    event_fqn: str | None = None
    job_fqn: str | None = None
    method_fqn: str | None = None
    cache_key: str | None = None
    module_prefix: str | None = None
    policy_model_fqn: str | None = None
    interface_fqn: str | None = None


async def discover_targets(mcp: Any) -> Targets:
    """Probe the index to find a real arg for every parametrised tool."""
    t = Targets()

    routes = await _call(mcp, "list_routes", {})
    if routes and routes.get("routes"):
        first = routes["routes"][0]
        t.route_uri = first.get("uri")
        if first.get("methods"):
            t.route_method = first["methods"][0]

    for kind, attr in (
        ("model", "model_fqn"),
        ("class", "class_fqn"),
        ("event", "event_fqn"),
        ("job", "job_fqn"),
    ):
        result = await _call(mcp, "list_by_kind", {"kind": kind})
        if result and result.get("items"):
            setattr(t, attr, result["items"][0].get("fqn"))

    modules = await _call(mcp, "list_modules", {"min_classes": 3})
    if modules and modules.get("modules"):
        t.module_prefix = modules["modules"][0].get("prefix")

    if t.class_fqn:
        described = await _call(mcp, "describe_class", {"fqn": t.class_fqn})
        if described and described.get("methods"):
            t.method_fqn = f"{t.class_fqn}::{described['methods'][0]['name']}"

    # Find a model that actually has a policy — pick the first
    # ``policy`` node and read its target via describe_class, falling
    # back to ``policies_applied_to`` reverse-lookup on each model.
    policies = await _call(mcp, "list_by_kind", {"kind": "policy"})
    if policies and policies.get("items"):
        # A policy's target model is recorded as an APPLIES_TO edge —
        # easiest to discover by walking models and checking which
        # has ``policies_applied_to`` populated. Cap at the first match.
        models = await _call(mcp, "list_by_kind", {"kind": "model"})
        if models and models.get("items"):
            for item in models["items"][:30]:
                fqn = item.get("fqn")
                if not fqn:
                    continue
                desc = await _call(mcp, "describe_class", {"fqn": fqn})
                if desc and desc.get("policies_applied_to"):
                    t.policy_model_fqn = fqn
                    break

    # An indexed interface = any ``class``-kind node whose attributes
    # mark it as an interface OR a class that has implementations
    # somewhere in the graph. Cheapest probe: enumerate the
    # ``find_implementations`` tool's outputs across a few candidates.
    classes = await _call(mcp, "list_by_kind", {"kind": "class"})
    if classes and classes.get("items"):
        for item in classes["items"][:200]:
            fqn = item.get("fqn") or ""
            if "Interface" in fqn or "Contract" in fqn:
                t.interface_fqn = fqn
                break

    # Cache keys aren't always emitted by the static analyser; probe
    # with a generous wildcard. ``find_cache_users`` validator rejects
    # empty strings, so we must pass at least one character.
    cache_users = await _call(mcp, "find_cache_users", {"key": "*"})
    if cache_users and cache_users.get("matched_keys"):
        t.cache_key = cache_users["matched_keys"][0]

    return t


# ---------------------------------------------------------------------------
# Tool invocations
# ---------------------------------------------------------------------------


@dataclass
class Probe:
    """One tool invocation in the smoke run."""

    tool: str
    args: dict[str, Any]
    skip_reason: str | None = None


def build_probes(t: Targets) -> list[Probe]:
    """Construct the list of probes once targets have been discovered."""
    return [
        Probe("list_routes", {}),
        Probe("list_scheduled_tasks", {}),
        Probe("list_by_kind", {"kind": "controller"}),
        Probe("list_modules", {"min_classes": 1}),
        Probe(
            "describe_module",
            {"prefix": t.module_prefix} if t.module_prefix else {},
            skip_reason=None if t.module_prefix else "no module prefix found",
        ),
        Probe("explore_entity", {"name": "User"}),
        Probe(
            "describe_class",
            {"fqn": t.class_fqn} if t.class_fqn else {},
            skip_reason=None if t.class_fqn else "no class FQN found",
        ),
        Probe(
            "get_model_context",
            {"fqn": t.model_fqn} if t.model_fqn else {},
            skip_reason=None if t.model_fqn else "no model FQN found",
        ),
        Probe("describe_flow", {"query": "customers"}),
        Probe(
            "trace_route",
            {"uri": t.route_uri} if t.route_uri else {},
            skip_reason=None if t.route_uri else "no route in index",
        ),
        Probe(
            "get_request_flow",
            {"uri": t.route_uri} if t.route_uri else {},
            skip_reason=None if t.route_uri else "no route in index",
        ),
        Probe("find_handlers", {"uri_glob": "/api/*"}),
        Probe(
            "find_listeners",
            {"event": t.event_fqn} if t.event_fqn else {},
            skip_reason=None if t.event_fqn else "no event in index",
        ),
        Probe(
            "find_dispatchers",
            {"event": t.event_fqn} if t.event_fqn else {},
            skip_reason=None if t.event_fqn else "no event in index",
        ),
        Probe(
            "find_event_chains",
            {"event": t.event_fqn, "max_depth": 2} if t.event_fqn else {},
            skip_reason=None if t.event_fqn else "no event in index",
        ),
        Probe(
            "find_jobs_dispatching",
            {"job": t.job_fqn} if t.job_fqn else {},
            skip_reason=None if t.job_fqn else "no job in index",
        ),
        Probe(
            "get_policy_for",
            {"model_fqn": t.policy_model_fqn or t.model_fqn} if (t.policy_model_fqn or t.model_fqn) else {},
            skip_reason=None if (t.policy_model_fqn or t.model_fqn) else "no model FQN found",
        ),
        Probe(
            "resolve_binding",
            {"abstract": t.interface_fqn} if t.interface_fqn else {},
            skip_reason=None if t.interface_fqn else "no interface/contract found",
        ),
        Probe(
            "find_implementations",
            {"interface_fqn": t.interface_fqn} if t.interface_fqn else {},
            skip_reason=None if t.interface_fqn else "no interface/contract found",
        ),
        Probe(
            "find_callers",
            {"method_fqn": t.method_fqn} if t.method_fqn else {},
            skip_reason=None if t.method_fqn else "no method FQN found",
        ),
        Probe(
            "expand_call_tree",
            {"method_fqn": t.method_fqn, "direction": "downstream", "max_depth": 2}
            if t.method_fqn
            else {},
            skip_reason=None if t.method_fqn else "no method FQN found",
        ),
        Probe(
            "find_cache_users",
            {"key": t.cache_key} if t.cache_key else {},
            skip_reason=None if t.cache_key else "no cache key in index",
        ),
        Probe("semantic_search", {"query": "customer authentication flow"}),
    ]


# ---------------------------------------------------------------------------
# Execution + reporting
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """One row in the perf summary."""

    tool: str
    status: str  # ok | error | over_budget | skipped | crashed
    duration_ms: float
    budget_ms: int
    error_code: str | None
    result_size: int | None
    note: str | None = None


async def run_probe(mcp: Any, probe: Probe, budget_ms: int) -> ProbeResult:
    """Execute one probe and time it."""
    if probe.skip_reason is not None:
        return ProbeResult(
            tool=probe.tool,
            status="skipped",
            duration_ms=0.0,
            budget_ms=budget_ms,
            error_code=None,
            result_size=None,
            note=probe.skip_reason,
        )
    start = time.perf_counter()
    try:
        result = await mcp.call_tool(probe.tool, probe.args)
    except Exception as exc:  # noqa: BLE001 — surfaced as a row, not a crash
        elapsed = (time.perf_counter() - start) * 1000.0
        return ProbeResult(
            tool=probe.tool,
            status="crashed",
            duration_ms=elapsed,
            budget_ms=budget_ms,
            error_code=type(exc).__name__,
            result_size=None,
            note=str(exc)[:80],
        )
    elapsed = (time.perf_counter() - start) * 1000.0

    payload = _result_to_dict(result)
    error_code = payload.get("error_code") if isinstance(payload, dict) else None
    result_size = _estimate_size(payload)

    # ``error_code`` is part of every tool's documented contract — a
    # structured "target not found" response is not a failure, just a
    # different return shape. We mark it ``ok-error`` so the perf table
    # makes the distinction explicit, but it does NOT count toward the
    # script's exit-code failure tally.
    if error_code is not None:
        status = "ok-error"
    elif elapsed > budget_ms:
        status = "over_budget"
    else:
        status = "ok"

    return ProbeResult(
        tool=probe.tool,
        status=status,
        duration_ms=elapsed,
        budget_ms=budget_ms,
        error_code=error_code,
        result_size=result_size,
    )


def _result_to_dict(result: Any) -> Any:
    """Normalise a FastMCP call_tool result into a plain dict."""
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
    if hasattr(result, "data") and result.data is not None:
        return result.data
    return result


def _estimate_size(payload: Any) -> int | None:
    """Length of the primary list field in the response, or None."""
    if not isinstance(payload, dict):
        return None
    for value in payload.values():
        if isinstance(value, list):
            return len(value)
    return None


async def _call(mcp: Any, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort tool call that returns a dict (or ``None`` on failure)."""
    try:
        result = await mcp.call_tool(tool, args)
    except Exception:  # noqa: BLE001
        return None
    payload = _result_to_dict(result)
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _format_table(rows: list[ProbeResult]) -> str:
    """Render a fixed-width table for the terminal."""
    headers = ("tool", "status", "ms", "budget", "size", "error_code", "note")
    widths = [
        max(len(headers[0]), max(len(r.tool) for r in rows)),
        max(len(headers[1]), max(len(r.status) for r in rows)),
        max(len(headers[2]), max(len(f"{r.duration_ms:.1f}") for r in rows)),
        max(len(headers[3]), max(len(str(r.budget_ms)) for r in rows)),
        max(len(headers[4]), max(len(str(r.result_size or "-")) for r in rows)),
        max(len(headers[5]), max(len(r.error_code or "-") for r in rows)),
        max(len(headers[6]), max(len(r.note or "") for r in rows)),
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    for r in rows:
        lines.append(
            fmt.format(
                r.tool,
                r.status,
                f"{r.duration_ms:.1f}",
                str(r.budget_ms),
                str(r.result_size if r.result_size is not None else "-"),
                r.error_code or "-",
                r.note or "",
            ),
        )
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    storage_root = Path(args.storage_root).expanduser()
    print(f"# Building engine for slug={args.slug!r} under {storage_root}")
    engine = build_engine(storage_root, args.slug)

    print("# Building MCP server in-process")
    mcp = build_mcp_server(engine)

    print("# Discovering real targets from the index")
    targets = await discover_targets(mcp)
    print(f"  route_uri      = {targets.route_uri}")
    print(f"  model_fqn      = {targets.model_fqn}")
    print(f"  class_fqn      = {targets.class_fqn}")
    print(f"  event_fqn      = {targets.event_fqn}")
    print(f"  job_fqn        = {targets.job_fqn}")
    print(f"  method_fqn     = {targets.method_fqn}")
    print(f"  cache_key      = {targets.cache_key}")
    print(f"  module_prefix  = {targets.module_prefix}")
    print(f"  policy_model   = {targets.policy_model_fqn}")
    print(f"  interface_fqn  = {targets.interface_fqn}")

    print("\n# Running probes")
    probes = build_probes(targets)
    budgets: dict[str, int] = {
        entry.name: entry.tool_class.latency_budget_ms
        for entry in engine.registry.tools()
    }

    rows: list[ProbeResult] = []
    for probe in probes:
        budget = budgets.get(probe.tool, 200)
        row = await run_probe(mcp, probe, budget)
        rows.append(row)
        print(
            f"  {row.tool:<22} {row.status:<11} {row.duration_ms:>7.1f} ms"
            f" / {row.budget_ms} ms   "
            f"size={row.result_size if row.result_size is not None else '-'}   "
            f"{row.error_code or ''}",
        )

    rows.sort(key=lambda r: r.tool)
    print("\n# Summary\n")
    print(_format_table(rows))

    # Failure ↦ crash or budget bust. Structured ``error_code`` responses
    # are the documented contract, not a fault.
    failures = [r for r in rows if r.status in {"over_budget", "crashed"}]
    if args.json_summary:
        Path(args.json_summary).write_text(
            json.dumps(
                [
                    {
                        "tool": r.tool,
                        "status": r.status,
                        "duration_ms": round(r.duration_ms, 2),
                        "budget_ms": r.budget_ms,
                        "error_code": r.error_code,
                        "result_size": r.result_size,
                        "note": r.note,
                    }
                    for r in rows
                ],
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"\n# {len(rows)} probes, {len(failures)} non-clean")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slug",
        default="synthesq-api",
        help="Project slug under <storage-root>/projects/.",
    )
    parser.add_argument(
        "--storage-root",
        default=str(Path.home() / ".nexus"),
        help="Storage root containing the projects/<slug>/ index.",
    )
    parser.add_argument(
        "--json-summary",
        default=None,
        help="Optional: write the per-tool result table as JSON to this path.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
