"""Manage the per-package scratch dir for Nexus-driven mode.

Layout:
    ~/.nexus/cache/package-builds/<vendor>--<name>/<version>/
    ├── composer.json   # generated
    ├── composer.lock
    ├── vendor/         # populated by composer install
    ├── testbench.yaml  # copied from target
    ├── workbench       # symlink to target/workbench (if exists)
    ├── reflection.json # extractor output (transient)
    └── manifest.json   # written ONLY after full success
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.adapters.package.composer_metadata import ComposerMetadata


@dataclass(frozen=True, slots=True)
class ScratchManifest:
    """Records the cache-hit signal. Absent => scratch is dirty/stale."""

    fingerprint: str
    composer_install_at: str
    extracted_at: str


def scratch_dir_for(meta: ComposerMetadata, *, base: Path) -> Path:
    """Compute the canonical scratch dir for a package + version.

    Args:
        meta: Composer metadata for the target package.
        base: Root cache directory (e.g. ``~/.nexus/cache``).

    Returns:
        ``base/package-builds/<slug>/<version>``
    """
    return base / "package-builds" / meta.slug / meta.version


class ScratchBuilder:
    """Owns the lifecycle of a single scratch directory.

    Args:
        scratch_dir: The directory that holds all generated artefacts for
            one package version. Created on demand via ``ensure_dir()``.
    """

    def __init__(self, scratch_dir: Path) -> None:
        self.scratch_dir = scratch_dir

    @property
    def composer_json_path(self) -> Path:
        """Path to the generated composer.json inside the scratch dir."""
        return self.scratch_dir / "composer.json"

    @property
    def testbench_yaml_path(self) -> Path:
        """Path to the copied testbench.yaml inside the scratch dir."""
        return self.scratch_dir / "testbench.yaml"

    @property
    def workbench_path(self) -> Path:
        """Path to the workbench symlink inside the scratch dir."""
        return self.scratch_dir / "workbench"

    @property
    def manifest_path(self) -> Path:
        """Path to the success manifest inside the scratch dir."""
        return self.scratch_dir / "manifest.json"

    @property
    def vendor_path(self) -> Path:
        """Path to the vendor directory created by ``composer install``."""
        return self.scratch_dir / "vendor"

    @property
    def testbench_bin(self) -> Path:
        """Path to the testbench binary installed by Composer."""
        return self.vendor_path / "bin" / "testbench"

    @property
    def reflection_json_path(self) -> Path:
        """Path to the transient extractor output."""
        return self.scratch_dir / "reflection.json"

    def ensure_dir(self) -> None:
        """Create the scratch directory (and parents) if absent."""
        self.scratch_dir.mkdir(parents=True, exist_ok=True)

    def generate_composer_json(self, meta: ComposerMetadata, extractor_root: Path) -> None:
        """Write a generated composer.json that wires the target + extractor.

        Uses path repositories (symlink mode) for both the target package and
        the extractor so no network access is required for local packages.

        Args:
            meta: Composer metadata for the target package.
            extractor_root: Root of the ``nick-nds/nexus-extractor`` Composer package.
        """
        # A top-level ``name`` is required: when Laravel boots in this
        # scratch dir, its ``PackageManifest`` reads the host
        # composer.json and crashes with "Undefined array key 'name'"
        # if the field is missing. The value is cosmetic - it just has
        # to exist and be a valid Composer name.
        #
        # The ``autoload.psr-4`` ``Workbench\\App\\`` entry makes the
        # symlinked workbench/ dir autoloadable so Testbench's booted
        # skeleton can load any Workbench service providers listed in
        # the target's testbench.yaml. These classes are intentionally
        # filtered back out post-extraction by NamespaceExclusionFilter
        # (decision #7) - but they must load cleanly during boot.
        composer: dict[str, object] = {
            "name": f"nexus-scratch/{meta.slug}",
            "description": (
                f"Nexus scratch dir for indexing {meta.full_name}@{meta.version}. "
                "Auto-generated; do not edit."
            ),
            "repositories": [
                {
                    "type": "path",
                    "url": str(meta.package_root.resolve()),
                    "options": {"symlink": True},
                },
                {
                    "type": "path",
                    "url": str(extractor_root.resolve()),
                    "options": {"symlink": True},
                },
            ],
            "require": {
                meta.full_name: "*",
                "nick-nds/nexus-extractor": "*",
                "orchestra/testbench": "^8.0|^9.0|^10.0|^11.0",
            },
            "autoload": {
                "psr-4": {
                    "Workbench\\App\\": "workbench/app/",
                },
            },
            "minimum-stability": "dev",
            "prefer-stable": True,
        }
        self.composer_json_path.write_text(
            json.dumps(composer, indent=2, sort_keys=False), encoding="utf-8"
        )

    def copy_testbench_yaml(self, meta: ComposerMetadata) -> None:
        """Copy testbench.yaml from the target package into the scratch dir.

        Args:
            meta: Composer metadata whose ``testbench_yaml`` path is the source.
        """
        shutil.copyfile(meta.testbench_yaml, self.testbench_yaml_path)

    def symlink_workbench_if_present(self, meta: ComposerMetadata) -> None:
        """Create a workbench symlink inside scratch if the target has one.

        Silently skips when the target's ``workbench/`` directory does not exist.
        Replaces an existing stale link if the scratch dir is being reused.

        Args:
            meta: Composer metadata whose ``package_root`` is inspected.
        """
        target = meta.package_root / "workbench"
        if not target.is_dir():
            return
        link = self.workbench_path
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(target, link)

    def run_composer_install(self) -> subprocess.CompletedProcess[str]:
        """Run ``composer install`` inside the scratch directory.

        ``--optimize-autoloader`` is required: by default Composer's
        autoloader resolves PSR-4 classes lazily and lists them only in
        ``autoload_psr4.php``. Nexus's ``ClassMapWalker`` (Phase B)
        walks the classmap built from ``autoload_classmap.php``; without
        optimization, the target package's own PSR-4 classes never
        appear there and the resulting index has zero class entries
        for the package. The optimize flag converts every PSR-4 entry
        into a classmap entry at install time.

        Returns:
            The completed process (caller inspects ``returncode``).
            Does not raise on non-zero exit - callers check the result.
        """
        return subprocess.run(
            ["composer", "install", "--no-interaction", "--optimize-autoloader"],
            cwd=self.scratch_dir,
            check=False,
            capture_output=True,
            text=True,
        )

    def write_manifest(self, manifest: ScratchManifest) -> None:
        """Atomically record a successful build by writing manifest.json.

        This file is the sole cache-hit signal. It must only be written after
        ``composer install`` and extraction have both succeeded.

        Args:
            manifest: Populated manifest to serialise.
        """
        self.manifest_path.write_text(
            json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8"
        )

    def read_manifest(self) -> ScratchManifest | None:
        """Read manifest.json and deserialise it, or return None if absent/corrupt.

        Returns:
            A ``ScratchManifest`` when the file exists and parses cleanly,
            ``None`` otherwise (treated as a cache miss by the orchestrator).
        """
        if not self.manifest_path.is_file():
            return None
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return ScratchManifest(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def is_cache_hit(self, fingerprint: str) -> bool:
        """Return True when a valid manifest exists with a matching fingerprint.

        Args:
            fingerprint: The current package fingerprint to compare against.

        Returns:
            ``True`` only when manifest is present and fingerprints match.
        """
        m = self.read_manifest()
        return m is not None and m.fingerprint == fingerprint
