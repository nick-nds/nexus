"""``nexus mcp`` — start the MCP server.

One subcommand:

* ``serve`` — start the FastMCP server using stdio (default) or HTTP/SSE.

Design reference: ``internal_docs/PHASE-5-interface-layer.md`` §D5.8.

stdio is the default transport so ``claude`` and ``cursor`` can spawn
Nexus directly with:

.. code-block:: json

    {
      "mcpServers": {
        "nexus": {
          "command": "nexus",
          "args": ["mcp", "serve"]
        }
      }
    }

SSE / HTTP transports are available for advanced setups (proxies, shared
server instances, multi-agent topologies).
"""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


@click.group(
    name="mcp",
    help="Start the Nexus MCP server.",
)
def mcp_group() -> None:
    """Parent group for MCP subcommands."""


@mcp_group.command(
    "serve",
    help="Start the MCP server (stdio by default, or --transport sse/http).",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "http"]),
    default="stdio",
    show_default=True,
    help="Transport to use: stdio (for agents), sse, or http.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind to (sse/http transports only).",
)
@click.option(
    "--port",
    type=int,
    default=8000,
    show_default=True,
    help="Port to bind to (sse/http transports only).",
)
@click.option(
    "--log-level",
    type=click.Choice(["debug", "info", "warning", "error"]),
    default=None,
    help="Log level for the MCP server (default: warning).",
)
@click.pass_obj
def serve_command(
    cli_ctx: CliContext,
    transport: str,
    host: str,
    port: int,
    log_level: str | None,
) -> None:
    """Start the MCP server and block until interrupted."""
    from nexus.interfaces.mcp import build_mcp_server  # noqa: PLC0415

    mcp = build_mcp_server(cli_ctx.engine())

    # Translate SIGTERM to KeyboardInterrupt so the try/except below handles
    # it the same way as SIGINT (Ctrl-C). This ensures a clean exit (code 0)
    # when the process is sent SIGTERM by an init system or agent host.
    def _sigterm_handler(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm_handler)

    from typing import Literal  # noqa: PLC0415

    transport_t: Literal["stdio", "http", "sse", "streamable-http"]
    try:
        if transport == "stdio":
            mcp.run(transport="stdio", show_banner=False)
        else:
            transport_t = transport  # type: ignore[assignment]
            mcp.run(
                transport=transport_t,
                host=host,
                port=port,
                show_banner=False,
                log_level=log_level,
            )
    except KeyboardInterrupt:
        # SIGINT (Ctrl-C) or SIGTERM translated to KeyboardInterrupt by the
        # runtime.  Exit cleanly without printing a traceback.
        raise SystemExit(0) from None
