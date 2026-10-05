"""``CliContext.engine`` only probes the embedder when asked to.

The probe is a live embed round-trip on every CLI call; structural tools
never use the embedder, so ``nexus query`` skips it for them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from nexus.adapters.storage import ProjectMeta
from nexus.interfaces.cli.context import CliContext
from nexus.interfaces.cli.main import main

# Nothing listens on the discard port, so a probe fails fast.
_UNREACHABLE_OLLAMA = "http://127.0.0.1:9"


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliContext:
    monkeypatch.setenv("OLLAMA_HOST", _UNREACHABLE_OLLAMA)
    (tmp_path / "config.yml").write_text(
        "schema_version: '1.0'\nembedder:\n  provider: ollama\n  model: nomic-embed-text\n"
    )
    cli_ctx = CliContext(storage_root=tmp_path, project_slug="demo", output_format="json")
    storage = cli_ctx.storage()
    storage.initialise()
    storage.write_meta(
        ProjectMeta(
            project_slug="demo",
            project_path=str(tmp_path),
            embedder_id="ollama:nomic-embed-text",
        ),
    )
    return cli_ctx


def test_engine_probes_embedder_by_default(ctx: CliContext) -> None:
    coverage = ctx.engine().context.coverage

    assert coverage is not None
    assert coverage.semantic_search_available is False
    assert "unreachable" in (coverage.semantic_search_unavailable_reason or "")


def test_engine_skips_probe_when_not_requested(ctx: CliContext) -> None:
    coverage = ctx.engine(probe_embedder=False).context.coverage

    assert coverage is not None
    assert coverage.semantic_search_available is None
    assert coverage.semantic_search_unavailable_reason is None


def test_structural_query_skips_probe(ctx: CliContext) -> None:
    result = CliRunner().invoke(
        main,
        [
            "--storage-root",
            str(ctx.storage_root),
            "--slug",
            "demo",
            "--format",
            "json",
            "query",
            "find_callers",
            "--method-fqn",
            "App\\Foo::bar",
        ],
    )

    assert json.loads(result.stdout)["coverage"]["semantic_search_available"] is None
