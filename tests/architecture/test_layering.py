"""Architecture tests: the core layer must not depend on adapters.

The Nexus architecture (see ``CLAUDE.md`` §"Architectural principles")
is layered:

* :mod:`nexus.core` holds pure domain logic.
* :mod:`nexus.adapters` holds concrete I/O implementations.
* The dependency arrow points from adapters → core, never the
  reverse.

This test walks every source file under ``nexus/core/`` and fails if
any of them imports ``nexus.adapters`` in any form. A clean failure at
this boundary is the difference between an architecture that stays
clean and one that rots slowly.

The test is AST-based rather than regex-based so comments and strings
containing the word ``nexus.adapters`` don't produce false positives.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
NEXUS_ROOT = REPO_ROOT / "nexus"
CORE_ROOT = NEXUS_ROOT / "core"
ADAPTERS_ROOT = NEXUS_ROOT / "adapters"


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.name != "__pycache__")


def _imported_modules(source: str) -> set[str]:
    """Return the set of fully-qualified module names imported by ``source``.

    Handles both ``import foo`` and ``from foo import ...`` forms.
    Relative imports are not expected in this project (ruff enforces
    absolute imports via TID252) but we handle them defensively by
    skipping them.

    Imports inside ``if TYPE_CHECKING:`` blocks are excluded - they're
    erased at runtime and don't constitute a real layering dependency.
    Type annotations crossing layer boundaries are idiomatic and don't
    create import cycles, so the layering rule shouldn't catch them.
    """
    tree = ast.parse(source)
    type_checking_imports: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    type_checking_imports.add(id(child))

    imports: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in type_checking_imports:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue
            imports.add(node.module)
    return imports


def _is_type_checking_test(test: ast.expr) -> bool:
    """Return True for the various spellings of ``TYPE_CHECKING`` guard."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


class TestCoreDoesNotImportAdapters:
    """The load-bearing rule: nothing under nexus/core/ may import from
    nexus/adapters/."""

    @pytest.mark.parametrize(
        "file_path",
        _python_files(CORE_ROOT),
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_no_adapter_import(self, file_path: Path) -> None:
        source = file_path.read_text(encoding="utf-8")
        imports = _imported_modules(source)

        offending = [name for name in imports if name.startswith("nexus.adapters")]

        assert not offending, (
            f"{file_path.relative_to(REPO_ROOT)} imports from nexus.adapters: "
            f"{offending}. The core layer must be pure - move the usage to an "
            f"adapter or invert the dependency via a protocol."
        )


class TestCoreDoesNotImportInterfaceOrPipeline:
    """Core must also not import the interface or pipeline layers.

    These layers are added in later phases but we lock the rule in now
    so a slip is caught immediately.
    """

    @pytest.mark.parametrize(
        "file_path",
        _python_files(CORE_ROOT),
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_no_downstream_layer_imports(self, file_path: Path) -> None:
        source = file_path.read_text(encoding="utf-8")
        imports = _imported_modules(source)

        forbidden_prefixes = ("nexus.pipeline", "nexus.interfaces", "nexus.plugins")
        offending = [
            name
            for name in imports
            if any(name.startswith(prefix) for prefix in forbidden_prefixes)
        ]

        assert not offending, (
            f"{file_path.relative_to(REPO_ROOT)} imports from a downstream layer: {offending}."
        )


class TestCoreProtocolsDoNotImportConcreteModules:
    """The protocols module must not import concrete implementations.

    Protocols describe shapes; they must not pull in the very thing
    they abstract over. A TYPE_CHECKING-guarded import is fine (the
    types are strings at runtime); anything else is a smell.
    """

    def test_protocols_top_level_imports_are_stdlib_only(self) -> None:
        protocols_path = CORE_ROOT / "protocols.py"
        source = protocols_path.read_text(encoding="utf-8")

        tree = ast.parse(source)
        # Collect only module-level imports (not those inside
        # if TYPE_CHECKING: blocks).
        top_level_imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                top_level_imports.add(node.module)

        # Only standard-library or __future__ imports should be at the
        # top level. Nexus modules are allowed too as long as they are
        # themselves pure - but the simpler rule "stdlib only" is easy
        # to enforce and unambiguous.
        allowed_top_level = {"__future__", "typing", "collections.abc", "pathlib"}

        forbidden = [
            name
            for name in top_level_imports
            if name.split(".")[0] not in {m.split(".")[0] for m in allowed_top_level}
        ]
        assert not forbidden, (
            f"nexus/core/protocols.py imports non-stdlib modules at the top "
            f"level: {forbidden}. Move them inside `if TYPE_CHECKING:`."
        )


class TestAdaptersCanImportCore:
    """The reverse direction IS allowed - adapters depend on core.

    This test is a sanity check: at least one adapter imports from
    core. If this stops being true, something has gone wrong with the
    layering.
    """

    def test_some_adapter_imports_from_core(self) -> None:
        found = False
        for file_path in _python_files(ADAPTERS_ROOT):
            source = file_path.read_text(encoding="utf-8")
            imports = _imported_modules(source)
            if any(name.startswith("nexus.core") for name in imports):
                found = True
                break

        assert found, (
            "No adapter imports from nexus.core. Either the adapters are "
            "not using the core protocols (architectural problem) or the "
            "test traversal is broken."
        )


PACKAGE_ADAPTERS_ROOT = ADAPTERS_ROOT / "package"
PIPELINE_ROOT = NEXUS_ROOT / "pipeline"


class TestPackageAdaptersDoNotImportInterfaces:
    """Phase 5.5 package adapters must not import the interface layer.

    ``nexus.adapters.package.*`` sits below the interface layer in the
    dependency graph. If it imported ``nexus.interfaces`` we would have
    a cycle: interfaces → pipeline → adapters → interfaces.
    """

    @pytest.mark.parametrize(
        "file_path",
        _python_files(PACKAGE_ADAPTERS_ROOT),
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_adapters_package_does_not_import_interfaces(self, file_path: Path) -> None:
        source = file_path.read_text(encoding="utf-8")
        imports = _imported_modules(source)

        offending = [name for name in imports if name.startswith("nexus.interfaces")]

        assert not offending, (
            f"{file_path.relative_to(REPO_ROOT)} imports from nexus.interfaces: "
            f"{offending}. The adapters layer must not depend on the interface layer."
        )


class TestPackageIndexerDoesNotImportInterfaces:
    """``nexus.pipeline.package_indexer`` must not import the interface layer.

    The pipeline orchestrates adapters and core; the interface layer (CLI /
    MCP) sits *above* it. Importing nexus.interfaces from the pipeline would
    invert the dependency arrow and create a cycle.
    """

    def test_package_indexer_does_not_import_interfaces(self) -> None:
        package_indexer = PIPELINE_ROOT / "package_indexer.py"
        source = package_indexer.read_text(encoding="utf-8")
        imports = _imported_modules(source)

        offending = [name for name in imports if name.startswith("nexus.interfaces")]

        assert not offending, (
            f"nexus/pipeline/package_indexer.py imports from nexus.interfaces: "
            f"{offending}. The pipeline layer must not depend on the interface layer."
        )
