"""Build a :class:`fastmcp.FastMCP` instance from the :class:`ToolRegistry`.

Design reference: ``internal_docs/PHASE-5-interface-layer.md`` §D5.4.

Auto-generation approach
========================

For every :class:`~nexus.core.query.registry.RegisteredTool` in the
registry we:

1. Extract the Pydantic input model's fields to dynamically build a
   properly-typed Python function (using ``exec``). FastMCP infers the
   JSON schema from the function's annotations, so the MCP client sees
   correct field names and types.
2. The generated function's body dispatches to
   :meth:`~nexus.core.query.engine.QueryEngine.execute` and
   serialises the :class:`~nexus.core.query.base.ToolOutput` via
   ``model_dump(mode='json')``.
3. The handler is registered via ``FastMCP.add_tool`` using a
   ``FunctionTool`` so the MCP client sees the right schema.

Error handling
==============

``ToolNotFoundError`` and ``ToolInputError`` are turned into
``fastmcp.exceptions.ToolError`` so the MCP client receives a
structured error response. Any other exception propagates and FastMCP
will serialise it as a server error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.function_tool import FunctionTool
from pydantic_core import PydanticUndefined

from nexus.core.query.errors import ToolInputError, ToolNotFoundError
from nexus.core.query.trace import open_trace, trace_path_from_env
from nexus.logging import get_logger
from nexus.version import __version__

if TYPE_CHECKING:
    from nexus.core.query import QueryEngine

log = get_logger(__name__)


def build_mcp_server(engine: QueryEngine) -> FastMCP:
    """Construct a :class:`FastMCP` instance wired to *engine*.

    Every OSS tool in ``engine.registry`` becomes an MCP tool. Pro-tier
    tools are not registered here — they are injected by the pro plugin.

    Args:
        engine: A fully-constructed :class:`QueryEngine`.

    Returns:
        A :class:`FastMCP` ready to call ``.run()`` or ``.run_http_async()``.
    """
    mcp = FastMCP(
        name="nexus",
        instructions=(
            "Nexus — Laravel code intelligence. "
            "Use these tools to answer questions about the structure and "
            "behaviour of a Laravel codebase without reading every file."
        ),
        version=__version__,
    )

    # Opt-in trace: when ``NEXUS_TRACE_DIR`` is set we open one
    # JSONL trace for the lifetime of the server and feed every
    # tool dispatch through it. Helps operators debug agents that
    # got a wrong answer over MCP. Tracing is silent if the env var
    # is unset (the engine sees a NullQueryTrace).
    trace_path = trace_path_from_env()
    if trace_path is not None:
        trace = open_trace(trace_path)
        engine.set_trace(trace)
        log.info("mcp_trace_enabled", path=str(trace_path))

    # Eager-load the graph so the FIRST agent query doesn't pay the
    # ~500ms cold-cache penalty (SQLite materialisation of every
    # node + edge). The graph is process-cached after one ``load()``,
    # so subsequent tool calls reuse the in-memory copy.
    _warm_graph_cache(engine)

    for entry in engine.registry.tools():
        _register_tool(mcp, engine, entry.name, entry.description)

    return mcp


def _warm_graph_cache(engine: QueryEngine) -> None:
    """Trigger a single ``graph().load()`` to amortise cold-cache cost.

    Failures here are non-fatal — if the index is missing or the
    storage handle is unhealthy, the agent will see the real error
    on its first tool call. We just log a warning and continue.
    """
    try:
        engine.context.storage.graph().load()
    except Exception as exc:  # noqa: BLE001 — non-fatal warm-up
        log.warning("mcp_graph_warmup_failed", error_type=type(exc).__name__, error=str(exc))


def _register_tool(
    mcp: FastMCP,
    engine: QueryEngine,
    tool_name: str,
    description: str,
) -> None:
    """Register one tool entry as a FastMCP tool."""
    entry = engine.registry.get(tool_name)
    if entry is None:
        return

    fn = _build_handler(tool_name, engine, entry.tool_class.input_model)
    tool = FunctionTool.from_function(fn, name=tool_name, description=description)
    mcp.add_tool(tool)


def _build_handler(
    tool_name: str,
    engine: QueryEngine,
    input_model: type[Any],
) -> Any:
    """Dynamically build a typed handler function for *tool_name*.

    FastMCP 3.x infers the JSON schema from the function's annotations.
    We use ``exec`` to produce a function whose signature exactly matches
    the input model's fields (including types and defaults) so the MCP
    client sees the correct schema.

    The body of the generated function delegates to a ``_dispatch``
    closure bound in the exec namespace — this avoids having any
    tool-specific Python in the exec'd source.
    """
    fields = input_model.model_fields
    annotations = input_model.__annotations__

    param_parts: list[str] = []
    defaults: dict[str, object] = {}

    for name, field_info in fields.items():
        default = field_info.default
        if default is PydanticUndefined:
            param_parts.append(name)
        else:
            param_parts.append(f'{name}=_defaults_["{name}"]')
            defaults[name] = default

    params_str = ", ".join(param_parts)
    fn_src = (
        f"def _handler({params_str}):\n"
        f"    return _dispatch_(**{{k: v for k, v in locals().items()}})\n"
    )

    def _dispatch(**kwargs: Any) -> dict[str, Any]:
        try:
            output = engine.query(tool_name, kwargs)
        except ToolNotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except ToolInputError as exc:
            raise ToolError(str(exc)) from exc
        return dict(output.model_dump(mode="json"))

    ns: dict[str, Any] = {"_defaults_": defaults, "_dispatch_": _dispatch}
    exec(fn_src, ns)  # nosec: dynamic function built from controlled field names
    fn = ns["_handler"]

    # Attach type annotations so FastMCP's schema inference works.
    fn.__annotations__ = {name: annotations.get(name, Any) for name in fields}
    fn.__annotations__["return"] = dict
    fn.__name__ = tool_name

    return fn
