"""Embedder config precedence: project nexus.yml overrides global config.yml.

The embedder used for indexing is resolved from two places, in order:

1. ``<project>/nexus.yml`` ``embedder:`` block (project override, committed
   so a team standardises on one backend), then
2. ``<storage_root>/config.yml`` ``embedder:`` block (the user/machine default).

``_choose_embedder_spec`` returns the ``(provider, config)`` that wins, or
``None`` when neither source configures an embedder. It does not build the
embedder (that needs the backend package installed), so the precedence is
testable on its own.
"""

from __future__ import annotations

from pathlib import Path

from nexus.interfaces.cli.embedder import _choose_embedder_spec

_GLOBAL = (
    "schema_version: '1.0'\nembedder:\n  provider: fastembed\n  model: BAAI/bge-small-en-v1.5\n"
)
_PROJECT = (
    "schema_version: '1.0'\n"
    "project:\n  slug: demo\n"
    "embedder:\n  provider: ollama\n  model: nomic-embed-text\n  dimensions: '768'\n"
)
_PROJECT_NO_EMBEDDER = "schema_version: '1.0'\nproject:\n  slug: demo\nprofile: laravel-default\n"


def _layout(
    tmp_path: Path, *, global_yml: str | None, project_yml: str | None
) -> tuple[Path, Path]:
    storage = tmp_path / "store"
    storage.mkdir()
    if global_yml is not None:
        (storage / "config.yml").write_text(global_yml)
    project = tmp_path / "proj"
    project.mkdir()
    if project_yml is not None:
        (project / "nexus.yml").write_text(project_yml)
    return storage, project


def test_project_embedder_overrides_global(tmp_path: Path) -> None:
    storage, project = _layout(tmp_path, global_yml=_GLOBAL, project_yml=_PROJECT)

    spec = _choose_embedder_spec(storage, project)

    assert spec is not None
    provider, config = spec
    assert provider == "ollama"
    assert config.get("model") == "nomic-embed-text"


def test_falls_back_to_global_when_project_has_no_embedder(tmp_path: Path) -> None:
    storage, project = _layout(tmp_path, global_yml=_GLOBAL, project_yml=_PROJECT_NO_EMBEDDER)

    spec = _choose_embedder_spec(storage, project)

    assert spec is not None
    assert spec[0] == "fastembed"


def test_uses_global_when_no_project_nexus_yml(tmp_path: Path) -> None:
    storage, project = _layout(tmp_path, global_yml=_GLOBAL, project_yml=None)

    spec = _choose_embedder_spec(storage, project)

    assert spec is not None
    assert spec[0] == "fastembed"


def test_none_when_neither_configures_an_embedder(tmp_path: Path) -> None:
    storage, project = _layout(tmp_path, global_yml=None, project_yml=_PROJECT_NO_EMBEDDER)

    assert _choose_embedder_spec(storage, project) is None


def test_malformed_project_nexus_yml_falls_back_to_global(tmp_path: Path) -> None:
    storage, project = _layout(tmp_path, global_yml=_GLOBAL, project_yml="{ not: valid: yaml :")

    spec = _choose_embedder_spec(storage, project)

    assert spec is not None
    assert spec[0] == "fastembed"


def test_global_timeout_flows_into_spec(tmp_path: Path) -> None:
    global_yml = (
        "schema_version: '1.0'\n"
        "embedder:\n  provider: ollama\n  model: mxbai-embed-large\n  timeout_seconds: 600\n"
    )
    storage, project = _layout(tmp_path, global_yml=global_yml, project_yml=None)

    spec = _choose_embedder_spec(storage, project)

    assert spec is not None
    assert spec[1].get("timeout_seconds") == 600.0


def test_project_timeout_flows_into_spec(tmp_path: Path) -> None:
    project_yml = (
        "schema_version: '1.0'\n"
        "project:\n  slug: demo\n"
        "embedder:\n  provider: ollama\n  model: mxbai-embed-large\n  timeout_seconds: 600\n"
    )
    storage, project = _layout(tmp_path, global_yml=_GLOBAL, project_yml=project_yml)

    spec = _choose_embedder_spec(storage, project)

    assert spec is not None
    assert spec[0] == "ollama"
    assert spec[1].get("timeout_seconds") == 600
