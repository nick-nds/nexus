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


#: An embedder configuration: the provider name plus the per-provider
#: config dict (``model``, ``dimensions``, ...) passed to the factory.
EmbedderSpec = tuple[str, dict[str, object]]


def _global_embedder_spec(storage_root: Path) -> EmbedderSpec | None:
    """The embedder configured in ``<storage_root>/config.yml`` (user default)."""
    from nexus.config.global_config import load_global_config  # noqa: PLC0415

    config_path = storage_root / "config.yml"
    if not config_path.exists():
        return None
    cfg = load_global_config(config_path).embedder
    config: dict[str, object] = {"model": cfg.model}
    if cfg.dimensions is not None:
        config["dimensions"] = cfg.dimensions
    return (cfg.provider, config)


def _project_embedder_spec(project_path: Path) -> EmbedderSpec | None:
    """The embedder pinned in ``<project_path>/nexus.yml`` (project override).

    Returns ``None`` when there is no ``nexus.yml``, it has no ``embedder``
    block, or it fails to parse - the caller then falls back to the global
    config rather than aborting the run on a malformed project file.
    """
    from nexus.config.loader import ConfigError  # noqa: PLC0415
    from nexus.config.project_profile import load_project_profile  # noqa: PLC0415

    nexus_yml = project_path / "nexus.yml"
    if not nexus_yml.exists():
        return None
    try:
        profile = load_project_profile(nexus_yml)
    except ConfigError:
        return None
    embedder = profile.embedder
    if not embedder or not embedder.get("provider"):
        return None
    provider = embedder["provider"]
    config: dict[str, object] = {k: v for k, v in embedder.items() if k != "provider"}
    return (provider, config)


def _choose_embedder_spec(storage_root: Path, project_path: Path) -> EmbedderSpec | None:
    """Resolve the winning embedder spec: project override, then global default.

    Precedence (highest first): ``<project_path>/nexus.yml`` ``embedder:``
    block, then ``<storage_root>/config.yml`` ``embedder:`` block. Returns
    ``None`` when neither configures an embedder.
    """
    return _project_embedder_spec(project_path) or _global_embedder_spec(storage_root)


def _build_from_spec(spec: EmbedderSpec | None) -> Embedder | None:
    if spec is None:
        return None
    from nexus.adapters.embedders.registration import register_builtin_embedders  # noqa: PLC0415
    from nexus.plugins.registry import PluginRegistry  # noqa: PLC0415

    provider, config = spec
    registry = PluginRegistry()
    register_builtin_embedders(registry)
    try:
        return registry.resolve_embedder(provider, config)
    except KeyError:
        return None


def resolve_embedder(storage_root: Path, project_path: Path) -> Embedder | None:
    """Build the embedder for a project index run.

    Resolves project ``nexus.yml`` (override) before the global
    ``config.yml`` (default); see :func:`_choose_embedder_spec`. Returns
    ``None`` when no embedder is configured or the provider is unknown to
    the registry, in which case callers degrade to graph-only indexing.
    """
    return _build_from_spec(_choose_embedder_spec(storage_root, project_path))


def build_embedder_from_config(storage_root: Path) -> Embedder | None:
    """Resolve the embedder from the global ``<storage_root>/config.yml`` only.

    Used by ``nexus package index``, which has no project ``nexus.yml`` to
    override from. Project-mode indexing uses :func:`resolve_embedder`.
    Returns ``None`` when the config is absent or names an unknown provider.
    """
    return _build_from_spec(_global_embedder_spec(storage_root))


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
