"""Language-server adapters used by the indexing pipeline.

The :class:`~nexus.core.protocols.Lsp` protocol is satisfied by
backends here:

* :class:`NullLsp` — no-op fallback when no server is available.
* :class:`LspClient` — generic JSON-RPC stdio client; pair with
  :func:`resolve_lsp_binary` to auto-discover ``intelephense`` or
  ``phpactor`` on the host.
"""

from __future__ import annotations

from nexus.adapters.lsp.lsp_client import LspClient, LspProtocolError
from nexus.adapters.lsp.null_adapter import NullLsp
from nexus.adapters.lsp.resolver import resolve_lsp_binary

__all__ = ["LspClient", "LspProtocolError", "NullLsp", "resolve_lsp_binary"]
