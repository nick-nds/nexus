"""Read target package's composer.json + resolve identity for indexing."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class ComposerMetadataError(Exception):
    """Pre-flight failure reading or interpreting a target package's metadata.

    Each instance carries a stable ``code`` per the spec's error taxonomy.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ComposerMetadata:
    """Identity + paths for a target Composer package."""

    package_root: Path
    vendor: str
    name: str
    version: str
    psr4_namespaces: dict[str, str] = field(default_factory=dict)
    testbench_yaml: Path = field(default=Path())

    @property
    def full_name(self) -> str:
        """Return the fully-qualified Composer package name."""
        return f"{self.vendor}/{self.name}"

    @property
    def slug(self) -> str:
        """Return a filesystem-safe slug derived from the package name."""
        return f"{self.vendor}--{self.name}"


def read_composer_metadata(
    path: Path,
    *,
    name_override: str | None = None,
    version_override: str | None = None,
) -> ComposerMetadata:
    """Read and validate a target package's metadata.

    Args:
        path: Root directory of the target Composer package.
        name_override: Use this vendor/name instead of the one in composer.json.
        version_override: Use this version string instead of resolving it.

    Raises:
        ComposerMetadataError: with a stable ``code`` per the error taxonomy:
            - ``package_path_missing`` — ``path`` does not exist or is not a directory.
            - ``package_composer_missing`` — no composer.json found.
            - ``package_composer_invalid`` — composer.json is invalid JSON or missing ``name``.
            - ``package_testbench_yaml_missing`` — testbench.yaml not present.
            - ``package_version_unresolvable`` — no version in composer.json, no git tag, no branch.
    """
    if not path.is_dir():
        raise ComposerMetadataError(
            "package_path_missing",
            f"Path is not a directory: {path}",
        )

    composer_path = path / "composer.json"
    if not composer_path.is_file():
        raise ComposerMetadataError(
            "package_composer_missing",
            f"Path is not a Composer package — composer.json missing in {path}",
        )

    try:
        raw = composer_path.read_text(encoding="utf-8")
        composer: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ComposerMetadataError(
            "package_composer_invalid",
            f"composer.json is not valid JSON: {exc}",
        ) from exc

    name_value = name_override or composer.get("name")
    if not isinstance(name_value, str) or "/" not in name_value:
        raise ComposerMetadataError(
            "package_composer_invalid",
            "composer.json is missing a valid 'name' (expected '<vendor>/<name>').",
        )
    vendor, short = name_value.split("/", 1)

    testbench_yaml = path / "testbench.yaml"
    if not testbench_yaml.is_file():
        raise ComposerMetadataError(
            "package_testbench_yaml_missing",
            f"Nexus requires a testbench.yaml at {path}.",
        )

    autoload = composer.get("autoload")
    psr4_raw: object = {}
    if isinstance(autoload, dict):
        psr4_raw = autoload.get("psr-4", {})
    psr4: dict[str, str] = (
        {str(k): str(v) for k, v in psr4_raw.items()} if isinstance(psr4_raw, dict) else {}
    )

    version = version_override or _resolve_version(path, composer)

    return ComposerMetadata(
        package_root=path.resolve(),
        vendor=vendor,
        name=short,
        version=version,
        psr4_namespaces=psr4,
        testbench_yaml=testbench_yaml.resolve(),
    )


def _resolve_version(path: Path, composer: dict[str, object]) -> str:
    """Resolve a version string via composer.json -> git tag -> dev-<branch>.

    Raises:
        ComposerMetadataError: ``package_version_unresolvable`` when all strategies fail.
    """
    declared = composer.get("version")
    if isinstance(declared, str) and declared:
        return declared

    if (path / ".git").exists():
        tag = _git_exact_tag(path)
        if tag:
            return tag

        branch = _git_branch(path)
        if branch:
            return f"dev-{branch}"

    raise ComposerMetadataError(
        "package_version_unresolvable",
        (
            f"Cannot resolve package version: no 'version' in composer.json, "
            f"no git tag, no HEAD at {path}."
        ),
    )


def _git_exact_tag(path: Path) -> str | None:
    """Return the exact git tag for HEAD, or None."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        tag = result.stdout.strip()
        return tag if tag else None
    except subprocess.CalledProcessError:
        return None


def _git_branch(path: Path) -> str | None:
    """Return the current git branch name, or None if detached/unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else None
    except subprocess.CalledProcessError:
        return None
