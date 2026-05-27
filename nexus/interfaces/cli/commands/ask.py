"""``nexus ask`` - classifier-routed free-text query.

The ``ask`` command is the single entry point an agent or human
reaches for when they have a question but no specific tool in
mind. It runs the question through the :class:`QueryClassifier`,
executes the primary plan, and - if the primary plan returns a
structured error or an empty-looking result - walks the classifier's
ordered fallbacks until something answers or the list is exhausted.

When the only thing that answered was the semantic-search fallback
and even its top hit is below the
:data:`SEMANTIC_CONFIDENCE_FLOOR`, the command returns a structured
**refusal** (``error_code: "no_confident_match"``) instead of low-
quality hits. This is the explicit guardrail against the kind of
hallucination where the classifier picks a wrong tool and an agent
trusts the noisy fallback as the answer.

``--explain`` prints the classifier decision without executing the
tool, which is useful for debugging new rules and for CI snapshots.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from pydantic import BaseModel

from nexus.core.query import (
    QueryClassifier,
    ToolInputError,
    open_trace,
)
from nexus.core.query.trace import (
    record_ask_envelope,
    record_ask_refusal,
    record_classifier_decision,
)
from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from nexus.core.query import QueryPlan, QueryTrace
    from nexus.interfaces.cli.context import CliContext

#: Minimum vector score for a semantic-search hit to count as a
#: "confident match" when the classifier had to fall back from a
#: low-confidence rule. Below this, ``ask`` returns a structured
#: refusal instead of the weak hits.
SEMANTIC_CONFIDENCE_FLOOR = 0.65

#: A classifier plan with confidence below this is treated as
#: speculative - the only acceptable result is one that clears the
#: semantic-confidence floor above.
RULE_CONFIDENCE_FLOOR = 0.6

#: Tool names ``ask`` suggests in a refusal payload. Ordered roughly
#: by how often they're useful to a confused agent.
_REFUSAL_HINT_TOOLS: tuple[str, ...] = (
    "list_routes",
    "find_handlers",
    "describe_class",
    "find_callers",
    "trace_route",
    "semantic_search",
)


@click.command(name="ask", help="Classifier-routed free-text query.")
@click.argument("text", nargs=-1, required=True)
@click.option(
    "--explain",
    is_flag=True,
    default=False,
    help="Print the classifier plan instead of executing the tool.",
)
@click.option(
    "--trace",
    "trace_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Append a JSONL trace of the classifier decision and every tool "
        "dispatch to this file. Pair with `nexus trace inspect` for a "
        "human-readable summary."
    ),
)
@click.pass_obj
def ask_command(
    cli_ctx: CliContext,
    text: tuple[str, ...],
    explain: bool,
    trace_path: Path | None,
) -> None:
    """Run a natural-language question through the classifier.

    ``text`` is a positional sequence so the shell user can type
    ``nexus ask what happens on POST /orders`` without needing quotes.
    """
    query = " ".join(text).strip()
    if not query:
        print_error(cli_ctx, "ask requires a non-empty question")
        raise click.exceptions.Exit(2)

    classifier = QueryClassifier()
    plan = classifier.classify(query)

    if explain:
        render(cli_ctx, _plan_to_dict(plan))
        return

    floor = _resolve_semantic_floor(cli_ctx)
    with open_trace(trace_path) as trace:
        record_classifier_decision(trace, query=query, plan=plan)
        # Wire the trace into the engine for the duration of this run so
        # every tool dispatch (including fallbacks) lands in the file.
        engine = cli_ctx.engine()
        engine.set_trace(trace)
        try:
            result = _run_plan(
                cli_ctx,
                plan,
                query,
                semantic_floor=floor,
                trace=trace,
            )
        finally:
            engine.set_trace(None)
    if result is None:
        print_error(
            cli_ctx,
            f"no tool returned a usable result for {query!r}",
            hint="try a more specific question or use `nexus query <tool>`",
        )
        raise click.exceptions.Exit(1)
    render(cli_ctx, result)


def _resolve_semantic_floor(cli_ctx: CliContext) -> float:
    """Read the configured ``ask.semantic_confidence_floor`` or fall back.

    Loads the global config lazily so commands that don't go through
    the classifier don't pay the YAML-parse cost. Falls back to the
    module-level :data:`SEMANTIC_CONFIDENCE_FLOOR` constant when the
    config file is absent - keeping the out-of-the-box experience
    unchanged.
    """
    from nexus.config.global_config import load_global_config  # noqa: PLC0415

    config_path = cli_ctx.storage_root / "config.yml"
    if not config_path.exists():
        return SEMANTIC_CONFIDENCE_FLOOR
    cfg = load_global_config(config_path)
    return cfg.ask.semantic_confidence_floor


def _run_plan(
    cli_ctx: CliContext,
    plan: QueryPlan,
    query: str,
    *,
    semantic_floor: float = SEMANTIC_CONFIDENCE_FLOOR,
    trace: QueryTrace | None = None,
) -> dict[str, Any] | None:
    """Execute ``plan`` and fall through to fallbacks on error.

    Walks the primary plan and its fallback chain in order. Returns
    one of:

    * a wrapped envelope ``{tool, confidence, reason,
      alternatives_tried, result}`` when a confident result was found
      OR when only a low-confidence semantic match was available
      (the inner ``result`` is the structured refusal in the latter
      case), or
    * ``None`` if every plan errored out before producing any output.

    "Error" here means either a raised :class:`ToolInputError` (which
    a wrong rule-match might produce) or a structured output whose
    ``error`` field is set.
    """
    plans: list[QueryPlan] = [plan, *plan.fallbacks]
    tried: list[str] = []
    weak_result: tuple[QueryPlan, Any] | None = None
    for candidate in plans:
        try:
            result = cli_ctx.engine().query(candidate.tool, dict(candidate.args))
        except ToolInputError:
            tried.append(f"{candidate.tool}: input error")
            continue
        if not _is_usable(result):
            tried.append(
                f"{candidate.tool}: error_code={getattr(result, 'error_code', '?')}",
            )
            continue
        if _is_confident(candidate, result, semantic_floor):
            if trace is not None:
                record_ask_envelope(
                    trace,
                    query=query,
                    final_tool=candidate.tool,
                    confidence=candidate.confidence,
                    reason=candidate.reason,
                    alternatives_tried=tried,
                )
            return _wrap_with_routing(candidate, result, tried)
        # Usable but weak: hold it in case nothing better comes,
        # then refuse with structured guidance below.
        if weak_result is None:
            weak_result = (candidate, result)

    if weak_result is not None:
        weak_plan, weak_payload = weak_result
        refusal = _refusal_payload(
            query=query,
            weak_plan=weak_plan,
            weak_result=weak_payload,
            semantic_floor=semantic_floor,
        )
        if trace is not None:
            record_ask_refusal(
                trace,
                query=query,
                weak_tool=weak_plan.tool,
                best_score=float(refusal.get("best_vector_score", 0.0)),
                threshold=semantic_floor,
                alternatives_tried=tried,
            )
        return _wrap_with_routing(weak_plan, refusal, tried)
    return None


def _wrap_with_routing(
    plan: QueryPlan,
    result: Any,
    alternatives_tried: list[str],
) -> dict[str, Any]:
    """Annotate a tool result with the classifier's routing decision.

    The agent receives this wrapper so it can reason about why a
    particular tool ran. Pydantic results are dumped to JSON-friendly
    dicts; everything else (refusal payloads, plain dicts) passes
    through.
    """
    return {
        "tool": plan.tool,
        "confidence": plan.confidence,
        "reason": plan.reason,
        "alternatives_tried": list(alternatives_tried),
        "result": _to_jsonable(result),
    }


def _to_jsonable(payload: Any) -> Any:
    """Coerce a tool result into JSON-safe data without flattening."""
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json", by_alias=True)
    return payload


def _is_usable(result: Any) -> bool:
    """Return ``False`` for structured error payloads."""
    error_code = getattr(result, "error_code", None)
    return error_code is None


def _is_confident(
    plan: QueryPlan,
    result: Any,
    semantic_floor: float = SEMANTIC_CONFIDENCE_FLOOR,
) -> bool:
    """Decide whether a tool result clears the confidence bar.

    A non-``semantic_search`` tool - or a ``semantic_search`` plan
    that came from a high-confidence rule - is trusted unconditionally.
    The only case we second-guess is the bare semantic fallback that
    the classifier emits when no rule matched. The ``semantic_floor``
    is configurable via ``ask.semantic_confidence_floor`` in the
    user's ``~/.nexus/config.yml``; the parameter default keeps the
    helper testable in isolation.
    """
    if plan.tool != "semantic_search":
        return True
    if plan.confidence >= RULE_CONFIDENCE_FLOOR:
        return True
    hits = getattr(result, "hits", None) or []
    if not hits:
        return False
    best = max((getattr(hit, "vector_score", 0.0) for hit in hits), default=0.0)
    return best >= semantic_floor


def _refusal_payload(
    *,
    query: str,
    weak_plan: QueryPlan,
    weak_result: Any,
    semantic_floor: float = SEMANTIC_CONFIDENCE_FLOOR,
) -> dict[str, Any]:
    """Build the structured ``no_confident_match`` refusal.

    ``semantic_floor`` is parameterised so the message reports the
    threshold the agent's call was actually evaluated against, not
    a hardcoded constant that may be stale relative to the user's
    ``~/.nexus/config.yml``.
    """
    _ = weak_plan  # documented for future extensions; not currently used
    hits = getattr(weak_result, "hits", None) or []
    best = max(
        (getattr(hit, "vector_score", 0.0) for hit in hits),
        default=0.0,
    )
    return {
        "error_code": "no_confident_match",
        "error": (
            f"No confident match for {query!r}. The classifier did not match a "
            f"structural rule and the best semantic hit scored "
            f"{best:.2f} (threshold {semantic_floor:.2f})."
        ),
        "query": query,
        "best_vector_score": best,
        "weak_hits_count": len(hits),
        "suggested_tools": list(_REFUSAL_HINT_TOOLS),
        "hint": (
            "Try a more specific question (include a class FQN, route URI, "
            "or event name) or invoke a structural tool directly with "
            "`nexus query <tool>`."
        ),
    }


def _plan_to_dict(plan: QueryPlan) -> dict[str, Any]:
    """Recursively turn a :class:`QueryPlan` into a JSON-friendly dict."""
    return {
        "tool": plan.tool,
        "args": dict(plan.args),
        "confidence": plan.confidence,
        "reason": plan.reason,
        "fallbacks": [_plan_to_dict(fb) for fb in plan.fallbacks],
    }
