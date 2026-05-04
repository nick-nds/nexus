"""In-memory registry of query tools.

The registry is the single enumerable source of "tools Nexus
exposes". Phase 5's CLI walks it to generate Click subcommands;
Phase 5's MCP server walks it to register MCP tools; Phase 4's
classifier walks it for tool metadata when routing free-text
questions.

Each registered tool carries its schema + latency budget + an
identifying ``tier`` string (``"oss"`` by default; Phase 6's
pro tier registers its own tools with ``tier="pro"`` so the CLI
can filter when the license is absent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """One tool entry in the registry.

    The registry stores the *class*, not an instance. Tools are
    constructed per-query by the engine so each query gets a
    fresh instance that can safely cache state inside ``execute``
    without cross-run leakage.

    The tool class is typed as :class:`type` (plain, not
    ``type[Tool]``) because :class:`~nexus.core.query.tool_protocol.Tool`
    is a :class:`typing.Protocol` and mypy strict mode rejects
    ``type[Protocol]`` for plain classes that satisfy the protocol
    structurally. The engine still accesses every registered class's
    ``name``/``input_model``/``output_model``/``execute`` attributes
    and the protocol is honoured at runtime.
    """

    name: str
    description: str
    tool_class: type[Any]
    tier: str = "oss"


@dataclass(slots=True)
class ToolRegistry:
    """Mutable registry that every tool writes to at startup.

    The engine receives a finished registry from the application's
    bootstrap code (CLI/MCP entry point). Tests build their own
    registry with a subset of tools.
    """

    _tools: dict[str, RegisteredTool] = field(default_factory=dict)

    def register(
        self,
        tool_class: type[Any],
        *,
        tier: str = "oss",
    ) -> None:
        """Add a tool class to the registry.

        Raises :class:`ValueError` on duplicate names so a typo in
        one tool can't silently shadow another.
        """
        name = tool_class.name
        if name in self._tools:
            existing = self._tools[name]
            raise ValueError(
                f"Tool {name!r} is already registered (tier={existing.tier}); "
                f"duplicate registration refused.",
            )
        self._tools[name] = RegisteredTool(
            name=name,
            description=tool_class.description,
            tool_class=tool_class,
            tier=tier,
        )

    def get(self, name: str) -> RegisteredTool | None:
        """Return the registered tool by name or ``None``."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Return every registered tool name in sorted order."""
        return sorted(self._tools)

    def tools(self) -> list[RegisteredTool]:
        """Return every registered tool entry in name order."""
        return [self._tools[n] for n in self.names()]

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        """Whether ``name`` is a registered tool name."""
        return isinstance(name, str) and name in self._tools
