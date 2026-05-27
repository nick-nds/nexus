"""Rewrite reflection-document file paths to be <package_root>-relative.

The booted Testbench skeleton produces paths like::

    <scratch>/vendor/orchestra/testbench-core/laravel/vendor/<vendor>/<name>/src/X.php

which are useless to a user. This normalizer rewrites every path that
lives under ``vendor_path`` to a path relative to ``package_root``
(typically ``src/X.php``). Paths not under ``vendor_path`` pass through
unchanged.

The normalizer is idempotent: applying it twice yields the same result
(needed for the cross-mode idempotency guarantee - in-repo and
Nexus-driven modes produce byte-identical normalized output).

Decision #8: File paths in reflection.json are normalized to be
``<package_root>``-relative on Python ingest.

Sections normalized:
- ``classes.items[].reflection.file``
- ``static_analysis.findings[].file``
- ``routes.items[].action.file``
- ``gates_policies.gates[].callback.file``
- ``bindings.bindings[].concrete.file``
- ``events.listeners[].listeners[].file``  (ListenerCallback.file)
- ``project.base_path`` → ``str(package_root)``

Sections intentionally NOT normalized (no file-path fields):
- middleware, config, schedule, policies (model class name only)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nexus.core.reflection.document import (
        BindingsSection,
        ClassEntry,
        ClassesSection,
        EventListenerEntry,
        EventListenersSection,
        GatesPoliciesSection,
        ListenerCallback,
        ReflectionDocument,
        ReflectionSections,
        RoutesSection,
        StaticAnalysisSection,
    )


def normalize_paths(
    doc: ReflectionDocument,
    *,
    package_root: Path,
    vendor_path: Path,
) -> ReflectionDocument:
    """Return a new :class:`ReflectionDocument` with file paths rewritten.

    Args:
        doc: The document to normalize. May be a partially-populated
            package-mode document (some sections ``None``).
        package_root: Absolute path to the package's own source tree
            (the directory that contains ``composer.json``).
        vendor_path: Absolute path that was used as the package's
            location inside the scratch Testbench tree, e.g.
            ``/tmp/testbench-abc123/vendor/acme/foo``.

    Returns:
        A new ``ReflectionDocument`` instance; the input is not mutated
        (all Pydantic models are frozen).
    """
    rewriter = _PathRewriter(vendor_path)

    new_project = doc.project.model_copy(update={"base_path": str(package_root)})
    new_sections = doc.sections.model_copy(update=_section_updates(doc.sections, rewriter))
    return doc.model_copy(update={"project": new_project, "sections": new_sections})


class _PathRewriter:
    """Callable that rewrites a single path string. Idempotent."""

    def __init__(self, vendor_path: Path) -> None:
        # Resolve to eliminate any symlinks, then normalise to a forward-slash
        # prefix with a trailing separator so "startswith" can't falsely match
        # a sibling directory whose name shares a prefix (e.g. /vendor/foo-bar
        # vs /vendor/foo).
        self._prefix = vendor_path.resolve().as_posix().rstrip("/") + "/"

    def __call__(self, path: str | None) -> str | None:
        if path is None:
            return None
        normalized = Path(path).as_posix()
        if normalized.startswith(self._prefix):
            return normalized[len(self._prefix) :]
        return path


def _section_updates(sections: ReflectionSections, rewriter: _PathRewriter) -> dict[str, object]:
    """Build the dict of section updates passed to ``model_copy``.

    Only sections that are present in the document and carry file-path
    fields are included. ``model_copy`` on ``ReflectionSections`` ignores
    keys not present in the dict, so absent sections are left as-is.
    """
    updates: dict[str, object] = {}

    if sections.classes is not None:
        updates["classes"] = _rewrite_classes(sections.classes, rewriter)

    if sections.static_analysis is not None:
        updates["static_analysis"] = _rewrite_static_analysis(sections.static_analysis, rewriter)

    if sections.routes is not None:
        updates["routes"] = _rewrite_routes(sections.routes, rewriter)

    if sections.gates_policies is not None:
        updates["gates_policies"] = _rewrite_gates(sections.gates_policies, rewriter)

    if sections.bindings is not None:
        updates["bindings"] = _rewrite_bindings(sections.bindings, rewriter)

    if sections.events is not None:
        updates["events"] = _rewrite_events(sections.events, rewriter)

    # middleware: contains only class-name strings, no file paths - skip.
    # config:     arbitrary JSON blob, no file paths - skip.
    # schedule:   only command strings and descriptions - skip.

    return updates


def _rewrite_classes(section: ClassesSection, rewriter: _PathRewriter) -> ClassesSection:
    new_items: list[ClassEntry] = [
        item.model_copy(
            update={
                "reflection": item.reflection.model_copy(
                    update={"file": rewriter(item.reflection.file)}
                )
            }
        )
        for item in section.items
    ]
    return section.model_copy(update={"items": new_items})


def _rewrite_static_analysis(
    section: StaticAnalysisSection, rewriter: _PathRewriter
) -> StaticAnalysisSection:
    new_findings = [f.model_copy(update={"file": rewriter(f.file)}) for f in section.findings]
    return section.model_copy(update={"findings": new_findings})


def _rewrite_routes(section: RoutesSection, rewriter: _PathRewriter) -> RoutesSection:
    new_items = [
        item.model_copy(
            update={"action": item.action.model_copy(update={"file": rewriter(item.action.file)})}
        )
        for item in section.items
    ]
    return section.model_copy(update={"items": new_items})


def _rewrite_gates(section: GatesPoliciesSection, rewriter: _PathRewriter) -> GatesPoliciesSection:
    new_gates = [
        g.model_copy(
            update={"callback": g.callback.model_copy(update={"file": rewriter(g.callback.file)})}
        )
        for g in section.gates
    ]
    # policies only carry class names (not file paths) - pass through as-is.
    return section.model_copy(update={"gates": new_gates})


def _rewrite_bindings(section: BindingsSection, rewriter: _PathRewriter) -> BindingsSection:
    new_bindings = [
        b.model_copy(
            update={"concrete": b.concrete.model_copy(update={"file": rewriter(b.concrete.file)})}
        )
        for b in section.bindings
    ]
    # aliases and instances carry only class-name strings - pass through.
    return section.model_copy(update={"bindings": new_bindings})


def _rewrite_events(
    section: EventListenersSection, rewriter: _PathRewriter
) -> EventListenersSection:
    def _rewrite_entry(entry: EventListenerEntry) -> EventListenerEntry:
        new_listeners: list[ListenerCallback] = [
            lc.model_copy(update={"file": rewriter(lc.file)}) for lc in entry.listeners
        ]
        return entry.model_copy(update={"listeners": new_listeners})

    new_listeners = [_rewrite_entry(e) for e in section.listeners]
    new_wildcards = [_rewrite_entry(e) for e in section.wildcards]
    return section.model_copy(update={"listeners": new_listeners, "wildcards": new_wildcards})
