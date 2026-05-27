"""Entry-point-based plugin loader.

At application start, :func:`load_plugins` iterates every entry point
in the ``nexus.plugins`` group, imports the target module, looks up
its ``register`` function (or another name specified in the entry
point), and invokes it with the shared :class:`PluginRegistry`.

Plugins register themselves by publishing an entry point in their own
``pyproject.toml``:

.. code-block:: toml

    [project.entry-points."nexus.plugins"]
    my_plugin = "my_plugin:register"

``my_plugin:register`` must be a callable accepting exactly one
argument: the :class:`PluginRegistry` instance. The function may raise
any exception; the loader catches, wraps it as
:class:`PluginLoadError`, and either re-raises or records a warning
depending on the ``strict`` flag.

Testing
=======

Tests don't need to round-trip through Python's entry-point machinery.
They can construct a :class:`PluginRegistry` directly and call the
plugin's ``register`` function by hand. See
``tests/unit/test_plugins.py`` for the pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nexus.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "nexus.plugins"


class PluginLoadError(Exception):
    """A plugin failed to load.

    Catch this in the application entry point to distinguish plugin
    failures (which we want to report clearly) from other startup
    problems.
    """


@dataclass(frozen=True, slots=True)
class PluginLoadResult:
    """Summary of a :func:`load_plugins` invocation.

    Attributes:
        loaded: Names of plugins that registered successfully.
        failed: Mapping of plugin name → error message, for plugins
            that raised during registration.
    """

    loaded: tuple[str, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)


def load_plugins(
    registry: PluginRegistry,
    *,
    strict: bool = False,
    entry_point_group: str = ENTRY_POINT_GROUP,
) -> PluginLoadResult:
    """Discover and load every plugin declared in ``entry_point_group``.

    Args:
        registry: The shared :class:`PluginRegistry` each plugin writes
            into. Plugins are free to reject duplicate registrations;
            the loader does not deduplicate on their behalf.
        strict: If ``True``, any plugin that raises an exception during
            registration causes :func:`load_plugins` to re-raise it
            wrapped in :class:`PluginLoadError`. If ``False`` (default),
            the exception is recorded in the result's ``failed`` dict
            and loading continues - the CLI's ``nexus doctor`` flow
            wants to see all plugin failures at once.
        entry_point_group: Override for tests. Defaults to the public
            ``nexus.plugins`` group.

    Returns:
        A :class:`PluginLoadResult` summarising what loaded and what
        didn't. In strict mode this only returns on full success.
    """
    loaded: list[str] = []
    failed: dict[str, str] = {}

    group = entry_points(group=entry_point_group)
    for ep in group:
        try:
            _invoke_plugin(ep, registry)
        except Exception as e:
            message = f"{type(e).__name__}: {e}"
            failed[ep.name] = message
            log.exception("Plugin %s failed to load", ep.name)
            if strict:
                raise PluginLoadError(
                    f"Plugin {ep.name!r} failed to load: {message}",
                ) from e
            continue
        loaded.append(ep.name)

    return PluginLoadResult(loaded=tuple(loaded), failed=failed)


def _invoke_plugin(ep: EntryPoint, registry: PluginRegistry) -> None:
    """Load and call one plugin's registration function."""
    target = ep.load()
    if not callable(target):
        raise TypeError(
            f"Plugin {ep.name!r} entry point {ep.value!r} does not resolve "
            f"to a callable (got {type(target).__name__}).",
        )
    target(registry)
