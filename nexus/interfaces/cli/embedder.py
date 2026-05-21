"""Shared embedder-loading helper for CLI commands.

Both ``nexus index`` (project mode) and ``nexus package index`` need to
load the user's configured embedder from ``<storage_root>/config.yml``.
Without a shared helper they grew two near-identical implementations
that drifted (one warned on KeyError, the other was silent). This
module is the single source of truth for that resolution.

The helper is intentionally pure (no ``click.echo`` side effects) so
both interactive CLI commands and library callers can use it; the CLI
layer wraps it when it wants to surface a warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.core.protocols import Embedder


def build_embedder_from_config(storage_root: Path) -> Embedder | None:
    """Resolve the configured embedder from ``<storage_root>/config.yml``.

    Args:
        storage_root: Root of the Nexus storage directory (typically
            ``~/.nexus``).

    Returns:
        A constructed :class:`Embedder` when ``config.yml`` is present
        and names a known provider. ``None`` when the config file is
        absent or the configured provider is unknown to the plugin
        registry — callers degrade to graph-only indexing.
    """
    from nexus.adapters.embedders.registration import register_builtin_embedders  # noqa: PLC0415
    from nexus.config.global_config import load_global_config  # noqa: PLC0415
    from nexus.plugins.registry import PluginRegistry  # noqa: PLC0415

    config_path = storage_root / "config.yml"
    if not config_path.exists():
        return None

    global_cfg = load_global_config(config_path)
    registry = PluginRegistry()
    register_builtin_embedders(registry)

    embedder_cfg = global_cfg.embedder
    config_dict: dict[str, object] = {"model": embedder_cfg.model}
    if embedder_cfg.dimensions is not None:
        config_dict["dimensions"] = embedder_cfg.dimensions

    try:
        return registry.resolve_embedder(embedder_cfg.provider, config_dict)
    except KeyError:
        return None
