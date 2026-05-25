"""Tests for _resolve_git_head helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from nexus.pipeline.passes.embed_and_persist import _resolve_git_head


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
