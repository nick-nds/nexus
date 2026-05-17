"""Orchestrator: detect mode, run extraction, normalize paths, ingest.

After this orchestrator produces a normalized ReflectionDocument and
writes ProjectMeta, every existing tool (queries, MCP, semantic search)
works because the result is a regular project entry on disk.

Two modes (decision #7):

IN_REPO
    Both ``vendor/bin/testbench`` and ``vendor/nexus/extractor-php`` are
    already present in the target package. Call ``vendor/bin/testbench
    nexus:extract-package`` directly — no network, no scratch directory.

NEXUS_DRIVEN
    Either testbench or the extractor (or both) are missing. Build a
    scratch directory under ``cache_root``, generate a ``composer.json``
    that wires target + extractor via path repositories, run
    ``composer install``, then invoke testbench from there.

Both modes load the resulting reflection.json, validate that
``kind == "package"``, normalize paths to ``<package_root>``-relative,
then hand the normalized document to the Phase 2-4 pipeline
(BuildGraphPass → ChunkPass → EmbedAndPersistPass) by pre-populating
``ctx.reflection`` before the pipeline runs.
"""

from __future__ import annotations

import contextlib
import enum
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from nexus.adapters.package.fingerprint import compute_fingerprint
from nexus.adapters.package.path_normalizer import normalize_paths
from nexus.adapters.package.scratch_builder import (
    ScratchBuilder,
    ScratchManifest,
    scratch_dir_for,
)
from nexus.adapters.storage.project_storage import ProjectMeta, ProjectStorage
from nexus.core.reflection.loader import (
    ReflectionLoadError,
    load_reflection,
)
from nexus.pipeline.context import PipelineContext
from nexus.pipeline.factory import build_post_extraction_pipeline
from nexus.profiles.loader import load_builtin_profiles

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.adapters.embedders.cache import EmbeddingCache
    from nexus.adapters.package.composer_metadata import ComposerMetadata
    from nexus.core.graph.builder import GraphBuilder

log = structlog.get_logger(__name__)


class PackageIndexError(Exception):
    """Raised when package indexing cannot proceed.

    Each instance carries a stable ``code`` matching the spec's error
    taxonomy so callers can switch on the code without parsing the
    message string.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class IndexMode(enum.Enum):
    """Which extraction strategy the orchestrator will use."""

    IN_REPO = "in-repo"
    NEXUS_DRIVEN = "nexus-driven"


def detect_mode(path: Path) -> IndexMode:
    """Decide which extraction mode to use for the package at ``path``.

    Returns ``IN_REPO`` only when *both* ``vendor/bin/testbench`` and
    ``vendor/nexus/extractor-php`` are already present (the fast path).
    Falls back to ``NEXUS_DRIVEN`` for every other combination so that
    packages without the extractor installed always get a clean, isolated
    scratch build.

    Args:
        path: Root directory of the target Composer package.

    Returns:
        :attr:`IndexMode.IN_REPO` when the package is already set up,
        :attr:`IndexMode.NEXUS_DRIVEN` otherwise.
    """
    has_testbench = (path / "vendor" / "bin" / "testbench").is_file()
    has_extractor = (path / "vendor" / "nexus" / "extractor-php").is_dir()
    return IndexMode.IN_REPO if (has_testbench and has_extractor) else IndexMode.NEXUS_DRIVEN


@dataclass(frozen=True, slots=True)
class IndexResult:
    """Summary of a completed package indexing run.

    Attributes:
        slug: Filesystem-safe identifier (``<vendor>--<name>``).
        mode: Which extraction mode was used.
        project_dir: Absolute path to the per-project storage directory
            (``<nexus_root>/projects/<slug>/``).
        reflection_path: Absolute path to the reflection.json that was
            loaded and ingested.
    """

    slug: str
    mode: IndexMode
    project_dir: Path
    reflection_path: Path


class PackageIndexer:
    """Orchestrates package extraction, normalization, and pipeline ingestion.

    Args:
        cache_root: Root directory for Nexus-driven scratch builds
            (e.g. ``~/.nexus/cache``).
        nexus_root: Root of Nexus project storage
            (e.g. ``~/.nexus``).
        extractor_root: Root of the ``nexus/extractor-php`` Composer
            package bundled with this Nexus installation.
        timeout_s: Maximum seconds allowed for each subprocess call
            (extraction and composer install). Defaults to 300.
        builder: Optional graph builder override for tests.
        cache: Optional embedding cache override for tests.
    """

    def __init__(
        self,
        *,
        cache_root: Path,
        nexus_root: Path,
        extractor_root: Path,
        timeout_s: int = 300,
        builder: GraphBuilder | None = None,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self.cache_root = cache_root
        self.nexus_root = nexus_root
        self.extractor_root = extractor_root
        self.timeout_s = timeout_s
        self._builder = builder
        self._cache = cache

    def index(self, meta: ComposerMetadata) -> IndexResult:
        """Run the full package indexing flow.

        1. Detect whether to run in-repo or Nexus-driven.
        2. Run extraction to produce a reflection.json.
        3. Load + validate the document (must be ``kind="package"``).
        4. Normalize paths to ``<package_root>``-relative.
        5. Run the Phase 2-4 pipeline (graph → chunk → embed+persist).
        6. Write ``ProjectMeta`` with full attribution from ``doc.package``.

        Args:
            meta: Composer metadata for the target package, as returned
                by :func:`~nexus.adapters.package.composer_metadata.read_composer_metadata`.

        Returns:
            An :class:`IndexResult` summarising the completed run.

        Raises:
            PackageIndexError: with a stable ``code`` for every failure
                category (extraction timeout, composer install failure,
                invalid reflection document, etc.).
        """
        mode = detect_mode(meta.package_root)
        log.info("package.index.mode", slug=meta.slug, mode=mode.value)

        if mode == IndexMode.IN_REPO:
            reflection_path = self._extract_in_repo(meta)
        else:
            reflection_path = self._extract_nexus_driven(meta)

        log.info("package.index.load_reflection", slug=meta.slug, path=str(reflection_path))
        try:
            doc = load_reflection(reflection_path)
        except ReflectionLoadError as exc:
            raise PackageIndexError(
                "package_reflection_invalid",
                f"Failed to load reflection.json: {exc}",
            ) from exc

        if doc.kind != "package":
            raise PackageIndexError(
                "package_reflection_invalid",
                f"Expected kind='package' in reflection.json, got {doc.kind!r}",
            )

        # Compute vendor_path: where the package lives inside the
        # Testbench vendor tree, so the path normalizer can strip
        # the scratch prefix.
        if mode == IndexMode.IN_REPO:
            vendor_path = meta.package_root / "vendor" / meta.vendor / meta.name
        else:
            vendor_path = (
                scratch_dir_for(meta, base=self.cache_root) / "vendor" / meta.vendor / meta.name
            )

        log.info("package.index.normalize_paths", slug=meta.slug)
        normalized = normalize_paths(doc, package_root=meta.package_root, vendor_path=vendor_path)

        # --- Phase 2-4 pipeline ingestion ---
        # Pre-populate ctx.reflection with the already-loaded, normalized
        # document and run the post-extraction pipeline (BuildGraphPass
        # → ChunkPass → EmbedAndPersistPass). EnrichWithLspPass is
        # omitted because there is no LSP available for a Testbench env.
        storage = ProjectStorage(root=self.nexus_root, slug=meta.slug)
        storage.initialise()

        # Write the **normalized** reflection.json into the project
        # storage dir so downstream consumers (``nexus index status``,
        # the integration suite, future tooling) see the same paths
        # the pipeline saw. The un-normalized extractor output lives
        # transiently in the scratch dir.
        dest = storage.reflection_path
        dest.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")

        # Use a minimal "generic" profile — packages do not need
        # convention-specific classification hints. Load from built-ins
        # and fall back to the first available profile if "generic" isn't
        # present (future-proof against profile set changes).
        builtin_profiles = load_builtin_profiles()
        profile = next(
            (p for p in builtin_profiles if p.name == "generic"),
            next(iter(builtin_profiles), None),
        )
        if profile is None:
            raise PackageIndexError(
                "package_no_profile",
                "No built-in profiles found; cannot build pipeline context.",
            )

        pipeline = build_post_extraction_pipeline(
            builder=self._builder,
            cache=self._cache,
        )

        ctx = PipelineContext(
            project_path=meta.package_root,
            storage=storage,
            profile=profile,
        )
        ctx.reflection = normalized

        log.info("package.index.pipeline_start", slug=meta.slug)
        result = pipeline.run(ctx)
        log.info(
            "package.index.pipeline_done",
            slug=meta.slug,
            ok=result.ok,
            passes=result.passes_run,
        )

        if not result.ok:
            first_error = ctx.errors[0] if ctx.errors else None
            raise PackageIndexError(
                "package_pipeline_failed",
                (
                    f"Pipeline failed on pass {result.crashed_pass or 'unknown'}: "
                    f"{first_error.message if first_error else 'no details'}"
                ),
            )

        last_commit = self._git_head(meta.package_root)
        package_meta = ProjectMeta(
            project_slug=meta.slug,
            project_path=str(meta.package_root.resolve()),
            kind="package",
            package=doc.package,
            build_mode=mode.value,
            source_path=str(meta.package_root.resolve()),
            last_indexed_commit=last_commit,
            indexed_at=datetime.now(UTC).isoformat(),
            node_count=len(ctx.graph.nodes) if ctx.graph is not None else None,
            edge_count=len(ctx.graph.edges) if ctx.graph is not None else None,
        )
        storage.write_meta(package_meta)
        log.info("package.index.meta_written", slug=meta.slug)

        with contextlib.suppress(Exception):
            storage.close()

        return IndexResult(
            slug=meta.slug,
            mode=mode,
            project_dir=storage.project_dir,
            # Return the normalized copy in storage, not the raw scratch
            # output — downstream consumers expect package-relative paths.
            reflection_path=dest,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_in_repo(self, meta: ComposerMetadata) -> Path:
        """Run extraction using the package's own vendor/bin/testbench.

        Args:
            meta: Composer metadata for the target package.

        Returns:
            Path to the written reflection.json.

        Raises:
            PackageIndexError: on timeout or non-zero exit.
        """
        out = meta.package_root / ".nexus-extract.json"
        log.info("package.index.extract_in_repo", slug=meta.slug, output=str(out))
        try:
            result = subprocess.run(
                [
                    str(meta.package_root / "vendor" / "bin" / "testbench"),
                    "nexus:extract-package",
                    f"--package={meta.full_name}",
                    f"--output={out}",
                    "--quiet-progress",
                ],
                cwd=meta.package_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PackageIndexError(
                "package_extraction_timeout",
                f"Extraction timed out after {self.timeout_s}s",
            ) from exc
        if result.returncode != 0:
            raise PackageIndexError(
                "package_extraction_failed",
                f"vendor/bin/testbench nexus:extract-package failed:\n{result.stderr[-2500:]}",
            )
        return out

    def _extract_nexus_driven(self, meta: ComposerMetadata) -> Path:
        """Build a scratch dir, install dependencies, then run extraction.

        Uses a content-based fingerprint to skip ``composer install`` when
        the scratch dir is already up-to-date (cache hit).

        Args:
            meta: Composer metadata for the target package.

        Returns:
            Path to the written reflection.json inside the scratch dir.

        Raises:
            PackageIndexError: on composer install failure, missing
                extractor, extraction timeout, or non-zero exit.
        """
        scratch_dir = scratch_dir_for(meta, base=self.cache_root)
        builder = ScratchBuilder(scratch_dir)

        extractor_composer = self.extractor_root / "composer.json"
        fingerprint = compute_fingerprint(meta, extractor_composer)

        if builder.is_cache_hit(fingerprint):
            log.info(
                "package.index.cache_hit",
                slug=meta.slug,
                fingerprint=fingerprint[:12],
            )
        else:
            log.info(
                "package.index.cache_miss",
                slug=meta.slug,
                fingerprint=fingerprint[:12],
            )
            builder.ensure_dir()
            builder.generate_composer_json(meta, self.extractor_root)
            builder.copy_testbench_yaml(meta)
            builder.symlink_workbench_if_present(meta)

            install_result = builder.run_composer_install()
            if install_result.returncode != 0:
                raise PackageIndexError(
                    "package_composer_install_failed",
                    f"composer install failed:\n{install_result.stderr[-2500:]}",
                )
            if not (builder.vendor_path / "nexus" / "extractor-php").is_dir():
                raise PackageIndexError(
                    "package_extractor_install_missing",
                    "composer install succeeded but vendor/nexus/extractor-php is absent. "
                    f"Run `composer why-not nexus/extractor-php` in {scratch_dir}",
                )

        log.info(
            "package.index.extract_nexus_driven",
            slug=meta.slug,
            output=str(builder.reflection_json_path),
        )
        try:
            result = subprocess.run(
                [
                    str(builder.testbench_bin),
                    "nexus:extract-package",
                    f"--package={meta.full_name}",
                    f"--output={builder.reflection_json_path}",
                    "--quiet-progress",
                ],
                cwd=scratch_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PackageIndexError(
                "package_extraction_timeout",
                f"Extraction timed out after {self.timeout_s}s",
            ) from exc
        if result.returncode != 0:
            raise PackageIndexError(
                "package_testbench_boot_failed",
                f"vendor/bin/testbench in scratch failed:\n{result.stderr[-2500:]}",
            )

        builder.write_manifest(
            ScratchManifest(
                fingerprint=fingerprint,
                composer_install_at=datetime.now(UTC).isoformat(),
                extracted_at=datetime.now(UTC).isoformat(),
            )
        )

        return builder.reflection_json_path

    @staticmethod
    def _git_head(path: Path) -> str | None:
        """Return the current HEAD commit SHA for a git repo, or None.

        Args:
            path: Root directory of the target package.

        Returns:
            The 40-character SHA string, or ``None`` when ``path`` is not
            a git repository or ``git rev-parse`` fails.
        """
        if not (path / ".git").exists():
            return None
        try:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            return None
