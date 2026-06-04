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
        registry - callers degrade to graph-only indexing.
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


#: Embedder providers ship in extras whose name differs from the provider
#: id; everything else uses ``[<provider>]`` directly.
_PROVIDER_EXTRAS: dict[str, str] = {"fastembed": "local-embeddings"}


def preflight_embedder(embedder: Embedder) -> str | None:
    """Probe the embedder before the expensive indexing passes run.

    The backends import their optional client package lazily (e.g.
    ``from ollama import Client``), so a missing extra or a downed daemon
    only surfaces on the first ``embed`` call - which is the final pass,
    after extraction and LSP enrichment have already burned minutes. This
    runs a one-string probe up front and translates the failure into an
    actionable remediation string.

    Args:
        embedder: The constructed embedder about to drive the embed pass.

    Returns:
        ``None`` when the embedder responds and indexing can proceed, or a
        human-readable remediation message naming the specific cause (extra
        to install, daemon to start, or model to pull) when it can't.
    """
    from nexus.adapters.embedders.errors import (  # noqa: PLC0415
        EmbedderConnectionError,
        EmbedderError,
        EmbedderModelNotFoundError,
    )

    try:
        embedder.embed(["preflight"])
    except (ModuleNotFoundError, ImportError) as exc:
        provider = embedder.model_id.split(":", 1)[0]
        extra = _PROVIDER_EXTRAS.get(provider, provider)
        return (
            f"The '{provider}' embedder backend is missing its Python package "
            f"({exc}). Install the matching extra:\n"
            f"    pip install 'nexus-php[{extra}]'"
        )
    except EmbedderModelNotFoundError as exc:
        return (
            f"The embedding model is not installed: {exc}. "
            f"Pull it first (for Ollama: `ollama pull <model>`)."
        )
    except EmbedderConnectionError as exc:
        return (
            f"The embedder daemon is unreachable: {exc}. "
            f"Start it (for Ollama: `ollama serve`) and retry."
        )
    except EmbedderError as exc:
        return f"The embedder probe failed: {exc}."
    return None
