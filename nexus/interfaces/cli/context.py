"""CLI-side context passed between Click commands.

A :class:`CliContext` is the adapter-layer equivalent of
:class:`~nexus.pipeline.context.PipelineContext`: it carries the
open project storage, a lazily-built :class:`QueryEngine`, and the
resolved display settings (TTY / JSON / pretty).

Commands receive the context through Click's ``pass_obj`` mechanism,
never through module globals. That keeps the tests trivially
parameterisable — they just build a context with a stub storage and
call ``runner.invoke(..., obj=ctx)``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from nexus.core.query import (
    QueryEngine,
    ResponseBudget,
    ToolRegistry,
)
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools

if TYPE_CHECKING:
    from nexus.adapters.storage import ProjectStorage
    from nexus.core.protocols import Embedder, ProjectStorageProtocol


DEFAULT_ROOT = Path.home() / ".nexus"
DEFAULT_SLUG = "default"


class OutputFormat:
    """Sentinel values for the ``--format`` flag."""

    AUTO = "auto"
    JSON = "json"
    PRETTY = "pretty"


@dataclass(slots=True)
class CliContext:
    """Runtime context for every Click command in this adapter.

    Attributes:
        storage_root: ``~/.nexus`` by default; overridable via
            ``--storage-root`` for tests and sandboxed runs.
        project_slug: Slug identifying the project-specific subdir
            under ``<storage_root>/projects/<slug>/``. Resolved from
            ``nexus.yml`` at startup when present.
        project_path: The current project working directory, i.e. the
            repository root the CLI was invoked from. Used by the
            pipeline to locate source files.
        output_format: ``auto`` (default), ``json``, or ``pretty``.
            ``auto`` renders pretty when stdout is a TTY and JSON
            when piped to a file or another process.
        color: ``None`` = auto, ``True`` = force, ``False`` = disable.
        verbose: ``--verbose`` increases log verbosity.
        yes: ``--yes`` / ``--non-interactive`` short-circuits
            confirmation prompts.
    """

    storage_root: Path = field(default_factory=lambda: DEFAULT_ROOT)
    project_slug: str = DEFAULT_SLUG
    project_path: Path = field(default_factory=Path.cwd)
    output_format: str = OutputFormat.AUTO
    color: bool | None = None
    verbose: bool = False
    yes: bool = False
    _storage: ProjectStorage | None = field(default=None, init=False, repr=False)
    _engine: QueryEngine | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def resolved_format(self) -> str:
        """Turn ``auto`` into a concrete format based on stdout TTY state.

        Pretty when stdout is a terminal and JSON otherwise, so piping
        to a file or another command produces machine-readable output
        without the caller having to remember the ``--json`` flag.
        Honours ``NEXUS_OUTPUT_FORMAT`` for ergonomic overrides and
        ``NO_COLOR`` for the standard opt-out convention.
        """
        explicit = os.environ.get("NEXUS_OUTPUT_FORMAT")
        if self.output_format != OutputFormat.AUTO:
            return self.output_format
        if explicit in {OutputFormat.JSON, OutputFormat.PRETTY}:
            return explicit
        return OutputFormat.PRETTY if sys.stdout.isatty() else OutputFormat.JSON

    def use_color(self) -> bool:
        """Whether Rich should emit ANSI colour codes.

        ``--color``/``--no-color`` wins; otherwise the standard
        ``NO_COLOR`` env var opts out; otherwise defaults to "yes
        when stdout is a TTY".
        """
        if self.color is not None:
            return self.color
        if os.environ.get("NO_COLOR"):
            return False
        return sys.stdout.isatty()

    # ------------------------------------------------------------------
    # Lazy handles
    # ------------------------------------------------------------------

    def storage(self) -> ProjectStorage:
        """Open the project storage lazily on first use."""
        if self._storage is None:
            # Import here to keep module import time cheap: the
            # storage adapter drags in lancedb, which is the single
            # heaviest dependency in the project.
            from nexus.adapters.storage import ProjectStorage  # noqa: PLC0415

            self._storage = ProjectStorage(
                root=self.storage_root,
                slug=self.project_slug,
            )
        return self._storage

    def close(self) -> None:
        """Release any open storage handles."""
        if self._storage is not None:
            self._storage.close()
            self._storage = None
        self._engine = None

    def engine(self) -> QueryEngine:
        """Build the query engine lazily on first use."""
        if self._engine is None:
            from nexus.core.query.coverage import Coverage  # noqa: PLC0415

            registry = ToolRegistry()
            register_builtin_tools(registry)
            storage = cast("ProjectStorageProtocol", self.storage())
            embedder = self._load_embedder()
            vector_dimensions = getattr(embedder, "dimensions", None) if embedder else None
            coverage = Coverage.from_meta(self.storage().read_meta())
            ctx = QueryContext(
                storage=storage,
                budget=ResponseBudget(),
                embedder=embedder,
                vector_dimensions=vector_dimensions,
                coverage=coverage,
            )
            self._engine = QueryEngine(registry, ctx)
        return self._engine

    def _load_embedder(self) -> Embedder | None:
        """Load the embedder from global config, or return None."""
        from nexus.adapters.embedders.registration import (  # noqa: PLC0415
            register_builtin_embedders,
        )
        from nexus.config.global_config import load_global_config  # noqa: PLC0415
        from nexus.plugins.registry import PluginRegistry  # noqa: PLC0415

        config_path = self.storage_root / "config.yml"
        if not config_path.exists():
            return None

        global_cfg = load_global_config(config_path)
        plugin_registry = PluginRegistry()
        register_builtin_embedders(plugin_registry)

        embedder_cfg = global_cfg.embedder
        config_dict: dict[str, object] = {"model": embedder_cfg.model}
        if embedder_cfg.dimensions is not None:
            config_dict["dimensions"] = embedder_cfg.dimensions

        try:
            return plugin_registry.resolve_embedder(embedder_cfg.provider, config_dict)
        except KeyError:
            return None
