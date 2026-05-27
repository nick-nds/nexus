"""Filesystem loader for the reflection.json document.

The loader is the only piece of :mod:`nexus.core.reflection` that performs
I/O. It is intentionally pure with respect to its arguments: pass it a
:class:`pathlib.Path` and it returns a :class:`ReflectionDocument` (or
raises a typed exception). No globals, no logging side effects.

Why a custom exception hierarchy instead of letting Pydantic exceptions
escape: callers - most notably the indexing pipeline - want to
distinguish "file isn't there" from "file isn't valid JSON" from "file is
valid JSON but for a schema we don't speak". Each maps to a different
remediation message in the CLI/MCP layer.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from nexus.core.reflection.document import SCHEMA_MAJOR, ReflectionDocument

if TYPE_CHECKING:
    from pathlib import Path


class ReflectionLoadError(Exception):
    """Base class for reflection-loader failures.

    Catch this in adapter / pipeline code when you want to handle "the
    document is unusable" without caring about the specific reason.
    """


class ReflectionNotFoundError(ReflectionLoadError):
    """The reflection.json file does not exist at the given path."""


class ReflectionParseError(ReflectionLoadError):
    """The file exists but isn't valid JSON or doesn't match the schema."""


class ReflectionVersionError(ReflectionLoadError):
    """The schema_version major is not one this Python build understands."""


def load_reflection(path: Path) -> ReflectionDocument:
    """Load and validate a reflection.json document from disk.

    Args:
        path: Absolute or relative path to a reflection.json file
            produced by ``php artisan nexus:extract``.

    Returns:
        A fully-validated :class:`ReflectionDocument`.

    Raises:
        ReflectionNotFoundError: ``path`` does not exist or is not a file.
        ReflectionParseError: ``path`` is not valid JSON, or the JSON does
            not match the document schema (a Pydantic ValidationError).
        ReflectionVersionError: the document's ``schema_version`` major
            does not match :data:`SCHEMA_MAJOR`. Bumping the major number
            is a deliberate breaking change.
    """
    if not path.is_file():
        raise ReflectionNotFoundError(f"Reflection document not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ReflectionParseError(f"Reflection document is not valid JSON: {path}: {e}") from e

    _check_schema_version(raw, source=path)

    try:
        return ReflectionDocument.model_validate(raw)
    except ValidationError as e:
        raise ReflectionParseError(
            f"Reflection document failed validation: {path}: {e}",
        ) from e


def _check_schema_version(raw: object, *, source: Path) -> None:
    if not isinstance(raw, dict):
        raise ReflectionParseError(
            f"Reflection document must be a JSON object: {source}",
        )

    version = raw.get("schema_version")
    if not isinstance(version, str):
        raise ReflectionParseError(
            f"Reflection document is missing schema_version: {source}",
        )

    try:
        major_str, _ = version.split(".", 1)
        major = int(major_str)
    except ValueError as e:
        raise ReflectionParseError(
            f"Reflection document has malformed schema_version {version!r}: {source}",
        ) from e

    if major != SCHEMA_MAJOR:
        raise ReflectionVersionError(
            f"Reflection document schema_version {version} is not compatible "
            f"with this Nexus build (expected major {SCHEMA_MAJOR}). "
            f"Upgrade or downgrade nick-nds/nexus-extractor to a matching major "
            f"version. Source: {source}",
        )
