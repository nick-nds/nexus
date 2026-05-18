"""E2E: index sample-package, query structural tools against the result.

Gated on both ``RUN_E2E=1`` **and** ``RUN_PACKAGE_INTEGRATION=1``.
The indexer needs a working ``composer`` + PHP binary to bootstrap
Testbench and run the PHP extractor, so the gate mirrors the
integration-test gate.

Test coverage:
* ``list_routes``   — the sample route is present; workbench route absent.
* ``describe_class`` — ``SampleModel`` is present with method ``children``.

``find_listeners`` is intentionally omitted: the event graph requires
FIRES/LISTENS edges that only appear after full wiring of the
SamplePackageServiceProvider. That wiring is exercised by the integration
tests; the E2E layer focuses on structural tools that are cheaper to
assert here.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.adapters.storage import ProjectStorage
from nexus.core.query import (
    QueryEngine,
    ResponseBudget,
    ToolRegistry,
)
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools
from nexus.pipeline.package_indexer import PackageIndexer

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTRACTOR_ROOT = REPO_ROOT / "packages" / "nexus-extractor-php"
SAMPLE_PACKAGE = EXTRACTOR_ROOT / "tests" / "fixtures" / "sample-package"

skip_unless_e2e = pytest.mark.skipif(
    os.getenv("RUN_E2E") != "1" or os.getenv("RUN_PACKAGE_INTEGRATION") != "1",
    reason=("Set RUN_E2E=1 and RUN_PACKAGE_INTEGRATION=1 to run package E2E tests."),
)


@skip_unless_e2e
def test_index_sample_package_then_query_structural_tools(tmp_path: Path) -> None:
    """Full pipeline: index sample-package, then run structural query tools.

    Exercises the ``nexus-driven`` path (no pre-installed vendor deps) which
    is the common case when Nexus is used as an external tool against a package
    the user doesn't want to modify.
    """
    fixture = tmp_path / "sample-package"
    shutil.copytree(SAMPLE_PACKAGE, fixture)

    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )
    meta = read_composer_metadata(fixture)
    result = indexer.index(meta)

    assert result.slug == "nexus-fixtures--sample", f"Unexpected slug: {result.slug!r}"

    # Build a query engine against the indexed project.
    # No embedder is needed — we only exercise structural (non-semantic) tools.
    storage = ProjectStorage(root=tmp_path / "nexus", slug=result.slug)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    engine = QueryEngine(registry, ctx)

    # ------------------------------------------------------------------
    # list_routes — sample route present, workbench route absent.
    # ------------------------------------------------------------------
    routes_out = engine.query("list_routes", {})
    uris = [r.uri for r in routes_out.routes]
    assert any("sample" in u for u in uris), (
        f"Package's own '/sample' route missing from the index; got: {uris}"
    )
    assert not any("workbench-fixture" in u for u in uris), (
        f"Workbench fixture route leaked into the index: {uris}"
    )

    # ------------------------------------------------------------------
    # describe_class — SampleModel present with method ``children``.
    # ------------------------------------------------------------------
    describe_out = engine.query(
        "describe_class",
        {"fqn": "NexusFixtures\\Sample\\Models\\SampleModel"},
    )
    assert describe_out.error is None, f"describe_class returned an error: {describe_out.error}"
    method_names = {m.name for m in describe_out.methods}
    assert "children" in method_names, (
        f"Expected method 'children' on SampleModel; got: {method_names}"
    )

    storage.close()
