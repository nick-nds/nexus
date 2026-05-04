"""Nexus — Laravel-specific code intelligence.

The :mod:`nexus` package is layered:

* :mod:`nexus.core` — pure domain logic. No I/O. No third-party services.
* :mod:`nexus.adapters` — concrete implementations of the protocols defined
  in :mod:`nexus.core.protocols` (storage, embedders, LSP clients, the
  PHP extractor subprocess wrapper).
* :mod:`nexus.config` — Pydantic models for the global config and project
  profile YAML files.
* :mod:`nexus.profiles` — built-in YAML profiles, profile loader, and
  auto-detector.
* :mod:`nexus.plugins` — entry-point based plugin registry used by the
  pro tier (and any third-party plugin) to extend the OSS surface without
  modifying it.
* :mod:`nexus.pipeline` — indexing pipeline orchestration (added in
  Phase 3).
* :mod:`nexus.interfaces` — CLI and MCP server (added in Phase 5).

The architectural rule is hard: code in :mod:`nexus.core` never imports
from :mod:`nexus.adapters`. The adapters depend on the core, not the
other way around. Tests in ``tests/architecture/`` enforce this.
"""

from nexus.version import __version__

__all__ = ["__version__"]
