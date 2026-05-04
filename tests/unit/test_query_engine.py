"""Tests for the query engine infrastructure.

Covers the engine façade, tool registry, response budget, and
error mapping. Individual tools have their own test files.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from nexus.core.query import (
    QueryEngine,
    ResponseBudget,
    Tool,
    ToolInput,
    ToolInputError,
    ToolNotFoundError,
    ToolOutput,
    ToolRegistry,
)
from nexus.core.query.context import QueryContext
from pydantic import Field

# ---------------------------------------------------------------------------
# Stub tools
# ---------------------------------------------------------------------------


class _EchoInput(ToolInput):
    value: str


class _EchoOutput(ToolOutput):
    echoed: str
    items: list[str] = Field(default_factory=list)
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)
    _trimmable_lists: ClassVar[tuple[str, ...]] = ("items",)


class _EchoTool:
    """Returns the input wrapped in the output."""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "Echo the input back."
    input_model: ClassVar[type[ToolInput]] = _EchoInput
    output_model: ClassVar[type[ToolOutput]] = _EchoOutput
    latency_budget_ms: ClassVar[int] = 5

    def execute(self, payload: _EchoInput, ctx: QueryContext) -> _EchoOutput:
        return _EchoOutput(echoed=payload.value)


class _BigListTool:
    """Returns a deliberately oversized list to exercise the budget."""

    name: ClassVar[str] = "big"
    description: ClassVar[str] = "Return a big list for budget testing."
    input_model: ClassVar[type[ToolInput]] = _EchoInput
    output_model: ClassVar[type[ToolOutput]] = _EchoOutput
    latency_budget_ms: ClassVar[int] = 5

    def execute(self, payload: _EchoInput, ctx: QueryContext) -> _EchoOutput:
        return _EchoOutput(
            echoed=payload.value,
            items=[f"item-{i}" for i in range(500)],
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_EchoTool)
    reg.register(_BigListTool)
    return reg


@pytest.fixture
def context(tmp_path: Path) -> QueryContext:
    # The query engine doesn't touch storage at all for these
    # tests; pass a stub and a default budget.
    class _StubStorage:
        pass

    return QueryContext(
        storage=_StubStorage(),  # type: ignore[arg-type]
        budget=ResponseBudget(max_list_items=100),
    )


@pytest.fixture
def engine(registry: ToolRegistry, context: QueryContext) -> QueryEngine:
    return QueryEngine(registry, context)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_register_and_lookup(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool)

        entry = reg.get("echo")
        assert entry is not None
        assert entry.tool_class is _EchoTool
        assert entry.tier == "oss"

    def test_duplicate_registration_rejected(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_EchoTool)

    def test_names_are_sorted(self) -> None:
        reg = ToolRegistry()
        reg.register(_BigListTool)
        reg.register(_EchoTool)
        assert reg.names() == ["big", "echo"]

    def test_contains(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool)
        assert "echo" in reg
        assert "nope" not in reg

    def test_len(self) -> None:
        reg = ToolRegistry()
        assert len(reg) == 0
        reg.register(_EchoTool)
        reg.register(_BigListTool)
        assert len(reg) == 2

    def test_pro_tier(self) -> None:
        reg = ToolRegistry()
        reg.register(_EchoTool, tier="pro")
        assert reg.get("echo").tier == "pro"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Engine dispatch
# ---------------------------------------------------------------------------


class TestEngineDispatch:
    def test_runs_registered_tool(self, engine: QueryEngine) -> None:
        result = engine.query("echo", {"value": "hi"})
        assert isinstance(result, _EchoOutput)
        assert result.echoed == "hi"

    def test_none_payload_is_empty_dict(self, engine: QueryEngine) -> None:
        with pytest.raises(ToolInputError):
            # _EchoInput requires `value`, so None payload should fail
            engine.query("echo", None)

    def test_unknown_tool_raises(self, engine: QueryEngine) -> None:
        with pytest.raises(ToolNotFoundError, match="Unknown tool"):
            engine.query("nope")

    def test_invalid_input_raises(self, engine: QueryEngine) -> None:
        with pytest.raises(ToolInputError):
            engine.query("echo", {"wrong_field": "x"})

    def test_unknown_tool_error_lists_alternatives(self, engine: QueryEngine) -> None:
        with pytest.raises(ToolNotFoundError, match="big, echo"):
            engine.query("nope")


# ---------------------------------------------------------------------------
# Response budget
# ---------------------------------------------------------------------------


class TestResponseBudget:
    def test_small_outputs_pass_through_unchanged(self, engine: QueryEngine) -> None:
        result = engine.query("echo", {"value": "hi"})
        assert not result.truncated
        assert result.items == []

    def test_oversized_list_is_trimmed(self, engine: QueryEngine) -> None:
        result = engine.query("big", {"value": "hi"})

        assert result.truncated
        assert len(result.items) == 100
        assert any("items:500→100" in t for t in result.truncated_lists)

    def test_custom_budget_changes_cap(self, registry: ToolRegistry, tmp_path: Path) -> None:
        class _StubStorage:
            pass

        ctx = QueryContext(
            storage=_StubStorage(),  # type: ignore[arg-type]
            budget=ResponseBudget(max_list_items=5),
        )
        engine = QueryEngine(registry, ctx)

        result = engine.query("big", {"value": "hi"})
        assert len(result.items) == 5
        assert result.truncated


# ---------------------------------------------------------------------------
# Tool protocol shape
# ---------------------------------------------------------------------------


class TestToolProtocol:
    def test_registered_tool_satisfies_protocol_structurally(self) -> None:
        # Not an isinstance check — the protocol is just a shape.
        # We assert that _EchoTool has every attribute the engine
        # and the registry need.
        assert _EchoTool.name == "echo"
        assert _EchoTool.description
        assert _EchoTool.input_model is _EchoInput
        assert _EchoTool.output_model is _EchoOutput
        assert _EchoTool.latency_budget_ms > 0
        assert callable(_EchoTool().execute)

    def test_tool_output_is_frozen(self) -> None:
        out = _EchoOutput(echoed="x")
        with pytest.raises((AttributeError, ValueError, TypeError)):
            out.echoed = "y"  # type: ignore[misc]

    def test_tool_protocol_exported_from_package(self) -> None:
        # Just verifies the public name exists; nothing else depends
        # on its runtime presence.
        assert Tool is not None
