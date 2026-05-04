"""The :class:`Tool` protocol plus shared input/output base types.

Every query tool implements this protocol. The registry (next
module) uses the protocol's metadata (``name``, ``description``,
``input_model``, ``output_model``, ``latency_budget_ms``) to expose
tools to Phase 5's CLI and MCP server without per-tool wiring code.

Why Pydantic on both sides
==========================

Input and output models both inherit from Pydantic ``BaseModel`` so:

* Agents calling the MCP server get JSON-schema validation for free.
* Bad input becomes a clear :class:`~pydantic.ValidationError` that
  the engine wraps into :class:`~nexus.core.query.errors.ToolInputError`.
* The CLI can introspect the model to auto-generate Click arguments
  in Phase 5.
* Outputs are frozen and trim-friendly for the response-budget step.
"""

from __future__ import annotations

from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_serializer

# Imported at runtime — Pydantic's frozen models need ``Coverage`` to
# be a fully resolved type at model-class build time, not a forward
# reference, so a ``TYPE_CHECKING`` import would break ``ToolOutput``
# subclass construction at runtime.  ``Any`` and ``Callable`` are also
# imported at runtime because the ``@model_serializer`` decorator
# inspects the function's annotations.
from nexus.core.query.coverage import Coverage  # noqa: TC001

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class ToolInput(BaseModel):
    """Base class for every tool's input model.

    Extra fields are rejected so a typo in a caller's arguments
    produces a clear validation error instead of a silent default.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolOutput(BaseModel):
    """Base class for every tool's output model.

    Frozen so the response-budget trimmer has to build new instances
    rather than mutating in place — forces the trimmer to be
    explicit about what it dropped.

    The ``coverage`` field is set by :class:`QueryEngine` after the
    tool returns; tools themselves leave it as ``None``. Agents read
    it to distinguish "no matches" from "feature not indexed".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    coverage: Coverage | None = Field(
        default=None,
        description=(
            "Index-level metadata attached by the query engine. "
            "Tells the agent whether CALLS edges are populated, what "
            "embedder was used, when the index was last built, etc. "
            "An empty result with ``coverage.calls_indexed=False`` is "
            "fundamentally different from one with True."
        ),
    )

    @model_serializer(mode="wrap")
    def _drop_null_coverage(
        self,
        handler: Callable[[BaseModel], dict[str, Any]],
    ) -> dict[str, Any]:
        """Serialise the model, omitting ``coverage`` when it's ``None``.

        Many tool outputs nest other ``ToolOutput`` rows (e.g.
        ``CallerRow`` inside ``FindCallersOutput``). Only the top-level
        response carries the engine-attached coverage; nested rows
        leave it at the default ``None``. Without this hook the JSON
        would carry ``"coverage": null`` on every row, which is
        cosmetic noise and a real source of confusion for agents
        reading the response.
        """
        result = handler(self)
        if isinstance(result, dict) and result.get("coverage") is None:
            result.pop("coverage", None)
        return result


class Tool(Protocol):
    """Structural shape every query tool satisfies.

    Concrete tools are regular classes that declare these as class
    attributes (names, models, budget) and implement
    :meth:`execute`. The registry iterates them uniformly.
    """

    #: Short stable identifier used by CLI + MCP. ``snake_case``.
    name: ClassVar[str]

    #: One-sentence human description for help output and for the
    #: MCP tool metadata. Should tell an agent *when* to use this
    #: tool, not what it's called.
    description: ClassVar[str]

    #: Pydantic class used to validate caller input.
    input_model: ClassVar[type[ToolInput]]

    #: Pydantic class used to validate the produced output.
    output_model: ClassVar[type[ToolOutput]]

    #: Soft latency budget in milliseconds. The engine logs a
    #: warning when a tool exceeds its budget; it does not abort
    #: the call. CI enforces these as upper bounds in the
    #: Phase 4 performance tests.
    latency_budget_ms: ClassVar[int]

    def execute(self, payload: ToolInput, ctx: QueryContext) -> ToolOutput:
        """Produce the answer for ``payload``.

        The engine has already validated ``payload`` against
        :attr:`input_model`, so the tool can assume it is the right
        shape. The tool must never raise for business "not found"
        cases — those map to a documented output shape (typically
        an ``error`` field with a code and an actionable hint).
        """
        ...
