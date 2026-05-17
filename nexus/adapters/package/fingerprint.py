"""Compute the content fingerprint that drives the package-build cache."""

from __future__ import annotations

import hashlib
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.adapters.package.composer_metadata import ComposerMetadata


def compute_fingerprint(meta: ComposerMetadata, extractor_composer_json: Path) -> str:
    """Return a deterministic SHA-256 of the inputs that should bust the cache.

    Inputs (per spec decision #6):
        - target composer.json content
        - target testbench.yaml content
        - extractor-php composer.json content
        - target source state (git HEAD + porcelain, or recursive src hash)
    """
    h = hashlib.sha256()

    composer_path = meta.package_root / "composer.json"
    h.update(b"composer:")
    h.update(_read_bytes(composer_path))

    h.update(b"testbench:")
    h.update(_read_bytes(meta.testbench_yaml))

    h.update(b"extractor:")
    h.update(_read_bytes(extractor_composer_json))

    h.update(b"source:")
    h.update(_source_state(meta).encode("utf-8"))

    return h.hexdigest()


def _read_bytes(p: Path) -> bytes:
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return b""


def _source_state(meta: ComposerMetadata) -> str:
    """Return a string capturing the target source's current state."""
    git_dir = meta.package_root / ".git"
    if git_dir.exists():
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=meta.package_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            porcelain = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=meta.package_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return f"git:{head}\nporcelain:{porcelain}"
        except subprocess.CalledProcessError:
            pass

    # Fall back to recursive hash of every autoload.psr-4 dir.
    h = hashlib.sha256()
    for rel_dir in sorted(set(meta.psr4_namespaces.values())):
        abs_dir = (meta.package_root / rel_dir).resolve()
        if not abs_dir.is_dir():
            continue
        for f in sorted(abs_dir.rglob("*.php")):
            try:
                h.update(f.relative_to(meta.package_root).as_posix().encode("utf-8"))
                h.update(b":")
                h.update(f.read_bytes())
                h.update(b"\n")
            except OSError:
                continue
    return f"hash:{h.hexdigest()}"
