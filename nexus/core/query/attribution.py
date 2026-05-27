"""Build the ``package`` envelope block + CLI footer for package-kind responses.

Per spec decision #10, every CLI/MCP query response against a project
where ``ProjectMeta.kind == "package"`` carries an attribution block
identifying the package and crediting its authors. This module is the
pure-data builder; CLI and MCP adapters call into it without touching
any tool's output schema (so the v1.0 freeze contract is preserved).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.adapters.storage.project_storage import ProjectMeta

#: Maximum number of authors to render inline before appending "+N more".
_AUTHOR_DISPLAY_LIMIT = 3


def build_attribution(meta: ProjectMeta) -> dict[str, Any] | None:
    """Return the ``package`` envelope block, or ``None`` for project-kind projects.

    Args:
        meta: Project metadata read from ``meta.json``.

    Returns:
        A plain dict suitable for JSON serialisation, or ``None`` when
        ``meta.kind`` is not ``"package"`` (including when ``meta.package``
        is ``None`` - defensive against malformed meta).
    """
    if meta.kind != "package" or meta.package is None:
        return None
    pkg = meta.package
    return {
        "vendor": pkg.vendor,
        "name": pkg.name,
        "version": pkg.version,
        "description": pkg.description,
        "authors": [a.model_dump() for a in pkg.authors],
        "license": pkg.license,
        "homepage": pkg.homepage,
    }


def render_attribution_footer(meta: ProjectMeta) -> str:
    """Render the multi-line attribution footer for ``--pretty`` CLI output.

    Returns an empty string when ``meta`` describes a project (not a package).
    Missing optional fields are gracefully omitted; the function never
    produces double separators (`` · ``) or trailing punctuation.

    The footer format is::

        Indexed from vendor/name@version
        by Author One <email>, Author Two <email>, Author Three +N more · MIT
        https://homepage

    Author list is capped at :data:`_AUTHOR_DISPLAY_LIMIT` entries.

    Args:
        meta: Project metadata read from ``meta.json``.

    Returns:
        A multi-line string (lines joined by newlines), or ``""`` for
        project-kind meta.
    """
    if meta.kind != "package" or meta.package is None:
        return ""
    pkg = meta.package

    line1 = f"Indexed from {pkg.vendor}/{pkg.name}@{pkg.version}"

    parts: list[str] = []
    if pkg.authors:
        head = pkg.authors[:_AUTHOR_DISPLAY_LIMIT]
        rendered = ", ".join(f"{a.name} <{a.email}>" if a.email else a.name for a in head)
        remainder = len(pkg.authors) - _AUTHOR_DISPLAY_LIMIT
        if remainder > 0:
            rendered += f" +{remainder} more"
        parts.append(f"by {rendered}")
    if pkg.license:
        parts.append(pkg.license)

    line2 = " · ".join(parts)
    line3 = pkg.homepage or ""

    return "\n".join(line for line in (line1, line2, line3) if line)
