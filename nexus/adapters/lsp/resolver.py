"""Locate an LSP server binary on the host.

The pipeline calls :func:`resolve_lsp_binary` to find a usable
language server. The lookup order, by design, is:

1. The explicit ``preferred`` argument (typically from a ``--lsp`` CLI
   flag), looked up via ``shutil.which``.
2. ``intelephense`` on PATH (``npm install -g intelephense``).
3. ``phpactor`` on PATH (system-wide install).
4. Mason's bin directory at
   ``~/.local/share/nvim/mason/bin/{intelephense,phpactor}``, since
   Neovim users typically install LSP servers there and they're not
   on ``$PATH``.

The function returns a tuple ``(binary_path, args)`` because each
server takes a different incantation to enter LSP stdio mode.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: Servers we know how to invoke, in fallback order.  Each entry is
#: ``(binary_name, lsp_stdio_args)``.
_KNOWN_SERVERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("intelephense", ("--stdio",)),
    ("phpactor", ("language-server",)),
)


def resolve_lsp_binary(
    preferred: str | None = None,
) -> tuple[str, tuple[str, ...]] | None:
    """Find the first usable LSP server on the host.

    Args:
        preferred: Explicit binary name or absolute path. When provided,
            this is tried first; if it points at an unknown server
            we still invoke it with no extra args.

    Returns:
        ``(binary_absolute_path, args)`` if a server was found, else
        ``None``. The pipeline substitutes :class:`NullLsp` on
        ``None``.
    """
    if preferred:
        binary, args = _resolve_preferred(preferred)
        if binary is not None:
            return binary, args

    for name, args in _KNOWN_SERVERS:
        found = shutil.which(name)
        if found is not None:
            return found, args

    mason_dir = Path.home() / ".local" / "share" / "nvim" / "mason" / "bin"
    for name, args in _KNOWN_SERVERS:
        candidate = mason_dir / name
        if candidate.is_file():
            return str(candidate), args

    return None


def _resolve_preferred(preferred: str) -> tuple[str | None, tuple[str, ...]]:
    """Resolve ``preferred`` against ``$PATH`` or as an absolute path.

    If the preferred name matches one of the known servers, the known
    invocation arguments are returned. Otherwise the binary is invoked
    with no extra args.
    """
    candidate = Path(preferred)
    if candidate.is_absolute() and candidate.is_file():
        return str(candidate), _args_for(candidate.name)

    found = shutil.which(preferred)
    if found is None:
        return None, ()
    return found, _args_for(Path(found).name)


def _args_for(binary_name: str) -> tuple[str, ...]:
    for known_name, args in _KNOWN_SERVERS:
        if binary_name == known_name:
            return args
    return ()
