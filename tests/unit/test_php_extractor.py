"""Tests for the PHP extractor subprocess adapter.

Strategy: we never shell out to real PHP. Instead each test writes a
tiny bash script that impersonates PHP, sets ``php_binary=...`` on
:class:`PhpExtractor`, and drives the rest of the real adapter logic
end-to-end. This verifies every code path without dragging in a PHP
interpreter or the Composer package.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from nexus.adapters.extractor import (
    ExtractorFailedError,
    ExtractorMissingError,
    ExtractorTimeoutError,
    PhpExtractor,
)


def _write_fake_php(tmp_path: Path, body: str) -> Path:
    """Write a bash script that impersonates PHP and return its path.

    The script sees the same arguments the real ``php`` binary would
    (``artisan nexus:extract --output PATH --quiet-progress ...``).
    """
    path = tmp_path / "bin" / "php"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "artisan").write_text("#!/usr/bin/env php\n")
    return project


def _output_path(tmp_path: Path) -> Path:
    return tmp_path / "out" / "reflection.json"


class TestHappyPath:
    def test_writes_output_and_returns_result(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        php = _write_fake_php(
            tmp_path,
            # Extract --output <path> and write a minimal JSON there.
            "while [[ $# -gt 0 ]]; do\n"
            '  case "$1" in\n'
            '    --output) echo "{}" > "$2"; shift 2 ;;\n'
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n",
        )

        extractor = PhpExtractor(php_binary=str(php), timeout_seconds=10)
        result = extractor.extract(project, output_path=output)

        assert result.exit_code == 0
        assert result.output_path == output
        assert output.is_file()
        assert output.read_text() == "{}\n"

    def test_stdout_is_captured(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        php = _write_fake_php(
            tmp_path,
            'echo "progress 1/9"\n'
            'echo "progress 9/9"\n'
            "while [[ $# -gt 0 ]]; do\n"
            '  case "$1" in\n'
            '    --output) echo "{}" > "$2"; shift 2 ;;\n'
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n",
        )

        extractor = PhpExtractor(php_binary=str(php))
        result = extractor.extract(project, output_path=output)

        assert "progress 1/9" in result.stdout
        assert "progress 9/9" in result.stdout


class TestMissingPhpBinary:
    def test_raises_when_binary_not_found(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)

        extractor = PhpExtractor(
            php_binary=str(tmp_path / "nonexistent_php"),
            timeout_seconds=5,
        )
        with pytest.raises(ExtractorMissingError):
            extractor.extract(project, output_path=output)

    def test_no_php_on_path_when_unspecified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        monkeypatch.setenv("PATH", "")

        extractor = PhpExtractor()
        with pytest.raises(ExtractorMissingError, match="No 'php' binary"):
            extractor.extract(project, output_path=output)


class TestMissingArtisan:
    def test_raises_when_not_a_laravel_root(self, tmp_path: Path) -> None:
        bare = tmp_path / "bare"
        bare.mkdir()
        output = _output_path(tmp_path)
        php = _write_fake_php(tmp_path, "exit 0\n")

        extractor = PhpExtractor(php_binary=str(php))
        with pytest.raises(ExtractorMissingError, match="artisan"):
            extractor.extract(bare, output_path=output)


class TestCommandNotRegistered:
    def test_remaps_laravel_unknown_command_error(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        # Laravel prints this to stderr + exits non-zero when a command
        # namespace isn't registered. We should remap to a clearer
        # ExtractorMissingError pointing at the composer install.
        php = _write_fake_php(
            tmp_path,
            'echo "There are no commands defined in the \\"nexus\\" namespace." >&2\nexit 1\n',
        )

        extractor = PhpExtractor(php_binary=str(php))
        with pytest.raises(ExtractorMissingError, match="composer require"):
            extractor.extract(project, output_path=output)


class TestNonZeroExit:
    def test_raises_with_stderr_preserved(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        php = _write_fake_php(
            tmp_path,
            'echo "Something exploded" >&2\nexit 1\n',
        )

        extractor = PhpExtractor(php_binary=str(php))
        with pytest.raises(ExtractorFailedError) as exc_info:
            extractor.extract(project, output_path=output)

        assert exc_info.value.exit_code == 1
        assert "Something exploded" in (exc_info.value.stderr or "")


class TestMissingOutput:
    def test_zero_exit_but_no_file(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        # Fake PHP exits 0 but never writes the file.
        php = _write_fake_php(tmp_path, "exit 0\n")

        extractor = PhpExtractor(php_binary=str(php))
        with pytest.raises(ExtractorFailedError, match="no output file"):
            extractor.extract(project, output_path=output)


class TestTimeout:
    def test_raises_after_timeout(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        php = _write_fake_php(tmp_path, "sleep 5\n")

        extractor = PhpExtractor(php_binary=str(php), timeout_seconds=0.3)
        with pytest.raises(ExtractorTimeoutError):
            extractor.extract(project, output_path=output)


class TestExtraArgs:
    def test_extra_args_are_passed_through(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        captured = tmp_path / "captured.txt"
        # Dump all arguments to a file so we can assert on them.
        php = _write_fake_php(
            tmp_path,
            f'echo "$@" > {captured}\n'
            "while [[ $# -gt 0 ]]; do\n"
            '  case "$1" in\n'
            '    --output) echo "{}" > "$2"; shift 2 ;;\n'
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n",
        )

        extractor = PhpExtractor(
            php_binary=str(php),
            extra_args=("--include-tests", "--vendor-allowlist=spatie/permission"),
        )
        extractor.extract(project, output_path=output)

        args = captured.read_text()
        assert "--include-tests" in args
        assert "--vendor-allowlist=spatie/permission" in args


class TestMultiWordPhpBinary:
    """php_binary can be a multi-word string like 'docker compose exec -T app php'.

    The extractor must shell-split it so the subprocess receives a
    proper argv list rather than treating the whole string as a binary name.
    """

    def test_docker_exec_style_binary_is_split_correctly(self, tmp_path: Path) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        captured = tmp_path / "argv0.txt"

        # We simulate "docker compose exec -T app php" with a wrapper script.
        # The wrapper writes its own name (argv[0]) to a file so we can assert
        # that it was invoked - not that the whole string was treated as one token.
        wrapper = tmp_path / "bin" / "fake-docker"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            # The wrapper plays the role of the whole chain: it finds --output
            # and writes the JSON, just like the real PHP would at the end of
            # the docker-exec chain.
            f"echo docker-exec-was-called > {captured}\n"
            "while [[ $# -gt 0 ]]; do\n"
            '  case "$1" in\n'
            '    --output) echo "{}" > "$2"; shift 2 ;;\n'
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n"
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        # Pass a multi-word string: "/<tmp>/bin/fake-docker compose exec -T app php"
        # shlex.split should break it into argv correctly.
        multi_word = f"{wrapper} compose exec -T app php"
        extractor = PhpExtractor(php_binary=multi_word)
        result = extractor.extract(project, output_path=output)

        assert result.exit_code == 0
        assert captured.read_text().strip() == "docker-exec-was-called"


class TestEnvironment:
    def test_xdebug_session_is_scrubbed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = _make_project(tmp_path)
        output = _output_path(tmp_path)
        captured = tmp_path / "env.txt"
        # Fake PHP dumps its environment to a file.
        php = _write_fake_php(
            tmp_path,
            f"env > {captured}\n"
            "while [[ $# -gt 0 ]]; do\n"
            '  case "$1" in\n'
            '    --output) echo "{}" > "$2"; shift 2 ;;\n'
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n",
        )

        monkeypatch.setenv("XDEBUG_SESSION", "debug")
        monkeypatch.setenv("SAFE_VAR", "keep-me")

        extractor = PhpExtractor(php_binary=str(php))
        extractor.extract(project, output_path=output)

        env_dump = captured.read_text()
        assert "SAFE_VAR=keep-me" in env_dump
        assert "XDEBUG_SESSION" not in env_dump
