"""Tests for _resolve_git_head helper."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.graph import Graph
from nexus.pipeline.context import PipelineContext
from nexus.pipeline.passes.embed_and_persist import EmbedAndPersistPass, _resolve_git_head


@dataclass(frozen=True)
class _StubProfile:
    name: str = "stub"
    custom_bases: dict[str, str] = field(default_factory=dict)
    custom_suffixes: dict[str, str] = field(default_factory=dict)


class TestResolveGitHead:
    """Unit tests for _resolve_git_head."""

    def test_returns_sha_in_git_repo(self, tmp_path: Path) -> None:
        """A real git repo with at least one commit returns a 40-char hex SHA."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@test.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@test.com",
                "HOME": str(tmp_path),
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            },
        )

        result = _resolve_git_head(tmp_path)

        assert result is not None
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_returns_none_when_not_a_git_repo(self, tmp_path: Path) -> None:
        """A plain directory with no .git returns None."""
        result = _resolve_git_head(tmp_path)

        assert result is None

    def test_returns_none_when_git_binary_missing(self, tmp_path: Path) -> None:
        """When git is not on PATH (FileNotFoundError), returns None."""
        with patch(
            "nexus.pipeline.passes.embed_and_persist.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = _resolve_git_head(tmp_path)

        assert result is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        """When the subprocess times out, returns None."""
        with patch(
            "nexus.pipeline.passes.embed_and_persist.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            result = _resolve_git_head(tmp_path)

        assert result is None


def test_write_meta_records_git_head(tmp_path: Path) -> None:
    """_write_meta populates last_indexed_commit from git HEAD."""
    # Set up a real git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
        },
    )

    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    graph = Graph()
    ctx = PipelineContext(
        project_path=tmp_path,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
    )

    EmbedAndPersistPass._write_meta(ctx, embedder_id=None)

    meta = storage.read_meta()
    assert meta is not None
    assert meta.last_indexed_commit is not None
    assert len(meta.last_indexed_commit) == 40


def test_write_meta_records_none_outside_git(tmp_path: Path) -> None:
    """_write_meta sets last_indexed_commit=None when not in a git repo."""
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    graph = Graph()
    ctx = PipelineContext(
        project_path=tmp_path,
        storage=storage,
        profile=_StubProfile(),
        graph=graph,
    )

    EmbedAndPersistPass._write_meta(ctx, embedder_id=None)

    meta = storage.read_meta()
    assert meta is not None
    assert meta.last_indexed_commit is None
