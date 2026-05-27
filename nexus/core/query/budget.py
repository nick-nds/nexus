"""Response-budget enforcement for tool outputs.

Every tool's output is passed through :meth:`ResponseBudget.trim`
before the engine returns it. The trimmer walks the output and
caps any list field declared in the output model's
``trimmable_lists`` class attribute at the configured per-list
ceiling. When a list is trimmed the model's ``truncated`` field
(if it exists) is set to ``True`` and a per-list note is added to
the ``truncated_lists`` field.

Why this matters
================

v1 of Nexus returned whole graphs for some queries, blowing past
agents' context windows and wasting tokens. The Phase 4 design
calls for hard-capped outputs with explicit truncation flags so
agents can ask a follow-up question ("give me the next 20
callers") rather than silently missing data.

Design: budget is per-run, not per-tool
=======================================

The default budget lives on the :class:`QueryContext` so callers
can temporarily lower it (interactive CLI in a narrow terminal)
or raise it (batch runs that write to disk). Tools don't read the
budget directly - they produce the full structured output and the
engine applies the budget as a post-step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from nexus.core.query.tool_protocol import ToolOutput

T = TypeVar("T", bound="ToolOutput")


@dataclass(frozen=True, slots=True)
class ResponseBudget:
    """Per-run output-size ceiling.

    Attributes:
        max_list_items: Maximum entries retained in any list field
            declared as trimmable on a tool output model. Default
            100 was chosen as a reasonable upper bound that keeps
            agent context usage sane while still covering most
            real-world request-flow outputs (a route with 10
            middlewares + 5 events fired + 3 jobs dispatched fits
            comfortably).
        max_string_chars: Cap on individual string field lengths.
            Unusually long values get truncated with an ellipsis
            suffix. Primarily protects against a pathological
            stack trace or source excerpt leaking into a response.
    """

    max_list_items: int = 100
    max_string_chars: int = 4000

    def trim(self, output: T) -> T:
        """Return a trimmed copy of ``output`` if anything was too big.

        Walks only the fields declared on the output model as
        trimmable (via ``_trimmable_lists`` class attribute - a
        tuple of field names). For each one, if the list exceeds
        :attr:`max_list_items`, build a new instance with the
        capped list and a note added to ``truncated_lists``.

        The method is deliberately minimal: only list-of-things
        trimming today. Nested structural trimming (e.g. trimming
        inside a dict of lists) can be added per-tool if a real
        output shape needs it.
        """
        trimmable: tuple[str, ...] = getattr(output, "_trimmable_lists", ())
        if not trimmable:
            return output

        updates: dict[str, object] = {}
        new_truncated_lists: list[str] = []

        for field in trimmable:
            value = getattr(output, field, None)
            if not isinstance(value, list):
                continue
            if len(value) > self.max_list_items:
                updates[field] = list(value[: self.max_list_items])
                new_truncated_lists.append(
                    f"{field}:{len(value)}→{self.max_list_items}",
                )

        if not updates:
            return output

        truncated_lists = list(getattr(output, "truncated_lists", []))
        truncated_lists.extend(new_truncated_lists)
        updates["truncated_lists"] = truncated_lists
        if hasattr(output, "truncated"):
            updates["truncated"] = True

        return output.model_copy(update=updates)
