"""Tests for _compute_changed_files incremental sync helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from nexus.interfaces.cli.commands._index_helpers import _compute_changed_files


def _git(cwd: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(cwd),
        "PATH": "/usr/bin:/bin",
    }
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, env=env)


def _git_head(cwd: Path) -> str:
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(cwd),
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with initial Foo.php and Bar.php."""
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init")
    (repo / "Foo.php").write_text("<?php class Foo {}")
    (repo / "Bar.php").write_text("<?php class Bar {}")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


class TestComputeChangedFiles:
    def test_returns_changed_php_files(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        baseline = _git_head(repo)

        (repo / "Foo.php").write_text("<?php class Foo { public function x() {} }")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "modify Foo")

        result = _compute_changed_files(repo, baseline)

        assert result is not None
        assert result == {(repo / "Foo.php").resolve()}

    def test_filters_out_non_php_files(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        baseline = _git_head(repo)

        (repo / "README.md").write_text("# hello")
        (repo / "app.js").write_text("console.log('hi')")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add non-php files")

        result = _compute_changed_files(repo, baseline)

        assert result is not None
        assert result == set()

    def test_includes_new_php_files(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        baseline = _git_head(repo)

        (repo / "New.php").write_text("<?php class New_ {}")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add New.php")

        result = _compute_changed_files(repo, baseline)

        assert result is not None
        assert (repo / "New.php").resolve() in result

    def test_includes_deleted_php_files(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        baseline = _git_head(repo)

        (repo / "Bar.php").unlink()
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "delete Bar.php")

        result = _compute_changed_files(repo, baseline)

        assert result is not None
        assert (repo / "Bar.php").resolve() in result

    def test_returns_none_when_no_baseline(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)

        result = _compute_changed_files(repo, None)

        assert result is None

    def test_returns_none_when_not_a_git_repo(self, tmp_path: Path) -> None:
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()

        result = _compute_changed_files(plain_dir, "abc123")

        assert result is None

    def test_returns_none_when_commit_unreachable(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)

        result = _compute_changed_files(repo, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

        assert result is None

    def test_returns_none_when_git_binary_missing(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        baseline = _git_head(repo)

        with patch(
            "nexus.interfaces.cli.commands._index_helpers.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = _compute_changed_files(repo, baseline)

        assert result is None

    def test_returns_empty_set_when_no_changes(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        baseline = _git_head(repo)

        result = _compute_changed_files(repo, baseline)

        assert result is not None
        assert result == set()
