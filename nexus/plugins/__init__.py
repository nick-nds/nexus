"""Plugin system: a registry + an entry-point-based loader.

Plugins extend Nexus without modifying its code. The pro tier
(:mod:`nexus_pro`) is the first and primary consumer, but any
third-party package can ship a plugin by publishing an entry point
under the ``nexus.plugins`` group.

The design (see ``internal_docs/02-monetization-and-open-core.md``):

* A plugin is a Python package that defines a top-level ``register``
  function taking a :class:`PluginRegistry`.
* The package declares an entry point in its own ``pyproject.toml``:
  ``[project.entry-points."nexus.plugins"] my_plugin = "my_plugin:register"``.
* On startup, Nexus calls :func:`load_plugins` which iterates every
  entry point in that group, imports each module, looks up its
  ``register`` attribute, and invokes it with the shared registry.
* Plugins contribute concrete implementations of the core protocols
  (embedders, stores, query tools) by calling ``registry.register_*``.

The OSS code in :mod:`nexus.core` never imports ``nexus_pro``. The
plugin system is the only boundary through which pro-tier code enters
the runtime.
"""

from nexus.plugins.loader import PluginLoadError, load_plugins
from nexus.plugins.registry import (
    EmbedderFactory,
    PluginRegistry,
    RegisteredEmbedder,
    RegisteredVectorStore,
    VectorStoreFactory,
)

__all__ = [
    "EmbedderFactory",
    "PluginLoadError",
    "PluginRegistry",
    "RegisteredEmbedder",
    "RegisteredVectorStore",
    "VectorStoreFactory",
    "load_plugins",
]
