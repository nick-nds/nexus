"""Subprocess wrapper for ``php artisan nexus:extract``.

The Python pipeline invokes the Phase 1 Composer package by spawning
a child PHP process, waiting for it, and reading the ``reflection.json``
the PHP side writes to a path we specify. Isolation from Laravel's
destructive application boot lives in the subprocess boundary itself;
this wrapper adds:

* **Timeout handling** - a configurable wall-clock limit so a hung
  boot doesn't block the pipeline forever.
* **Exit-code mapping** - translate Phase 1's documented exit codes
  (0 / 1 / 2 / non-zero on fatal) into typed exceptions.
* **Output verification** - if the command returns 0 but the output
  file wasn't written, raise :class:`ExtractorFailedError`.
* **Environment hygiene** - run with a minimal, deterministic env so
  runs are reproducible across user shells.

What it does NOT do
===================

* It does not parse the JSON. That's the job of
  :func:`nexus.core.reflection.load_reflection`.
* It does not retry. A flaky extraction is an infrastructure problem
  the user should see, not a transient we quietly paper over.
* It does not run PHP in-process. We never import PHP bindings.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nexus.adapters.extractor.errors import (
    ExtractorFailedError,
    ExtractorMissingError,
    ExtractorTimeoutError,
)

if TYPE_CHECKING:
    from pathlib import Path

# Exit codes documented in packages/nexus-extractor-php/README.md.
_EXIT_OK = 0
_EXIT_FATAL_DEFAULT = 1
_EXIT_USAGE_ERROR = 2


@dataclass(frozen=True, slots=True)
class ExtractorResult:
    """Outcome of a successful (or structurally-failed) extractor run.

    ``exit_code`` is captured even on success so callers can
    distinguish clean zero from fatal-with-partial-document (the
    shutdown handler path added in the Phase 1 review).
    """

    output_path: Path
    exit_code: int
    stdout: str
    stderr: str


class PhpExtractor:
    """Runs the Composer-provided ``nexus:extract`` Artisan command.

    Instances are cheap - construct one per indexing run. The
    configuration lives on the instance so the pipeline can set a
    different timeout or ``php`` binary path without touching the
    call site.
    """

    def __init__(
        self,
        *,
        php_binary: str | None = None,
        container_project_path: Path | None = None,
        timeout_seconds: float = 600.0,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        """Build an extractor runner.

        Args:
            php_binary: Override for the ``php`` executable. Defaults
                to whichever ``php`` is found on ``PATH``. Multi-word
                values are shell-split, so you can pass a Docker wrapper:
                ``"docker exec my-app php"``.
            container_project_path: When the Laravel project runs inside
                a container (Docker, Sail), pass the path where the project
                is mounted *inside* the container (e.g. ``Path("/var/www")``).
                Nexus uses the host ``project_path`` for local filesystem
                checks but translates paths to this container path before
                invoking PHP, so the subprocess sees valid in-container paths.
            timeout_seconds: Maximum wall-clock time before the
                subprocess is killed and :class:`ExtractorTimeoutError`
                is raised. Defaults to 10 minutes - generous enough
                for the largest projects we've seen (largeapp scale).
            extra_args: Additional CLI flags to pass through to the
                Artisan command (``--include-tests``, ``--include-vendor``,
                ``--vendor-allowlist=...``). The pipeline assembles these
                from the project profile before constructing the
                extractor.
        """
        self._php = php_binary
        self._container_project_path = container_project_path
        self._timeout = timeout_seconds
        self._extra_args = tuple(extra_args)

    def extract(self, project_path: Path, *, output_path: Path) -> ExtractorResult:
        """Run the extractor and return an :class:`ExtractorResult`.

        Args:
            project_path: The Laravel project's root directory (the
                one containing ``artisan`` and ``composer.json``).
            output_path: Absolute path where the extractor should
                write ``reflection.json``. Passed through as
                ``--output``. The caller owns directory creation.

        Raises:
            ExtractorMissingError: ``php`` is not on PATH, or the
                project does not register the ``nexus:extract`` command.
            ExtractorTimeoutError: the child process exceeded
                :attr:`timeout_seconds`.
            ExtractorFailedError: the child exited non-zero or wrote
                no output file.
        """
        php = self._resolve_php_binary()
        artisan = project_path / "artisan"
        if not artisan.is_file():
            raise ExtractorMissingError(
                f"No 'artisan' file found at {project_path}. "
                f"Is this the root of a Laravel project?",
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # When running PHP inside a container (docker exec, Sail, etc.) the
        # host paths are not valid inside the container.  We write the output
        # to a temp file *inside the project directory* (which is mounted into
        # the container) and translate both the artisan path and the output
        # path to their container equivalents before building the command.
        container = self._container_project_path
        if container is not None:
            artisan_arg = str(container / "artisan")
            # The temp file lives inside the project so the container can write
            # it; we move it to output_path (which may be outside the project)
            # after the subprocess exits.
            host_tmp = project_path / ".nexus-reflect.tmp.json"
            container_output_arg = str(container / ".nexus-reflect.tmp.json")
        else:
            artisan_arg = str(artisan)
            host_tmp = None
            container_output_arg = str(output_path)

        # Split multi-word binaries such as "docker exec my-app php"
        # so subprocess receives a proper argv list rather than a single token.
        php_argv = shlex.split(php)
        cmd = [
            *php_argv,
            artisan_arg,
            "nexus:extract",
            "--output",
            container_output_arg,
            "--quiet-progress",
            *self._extra_args,
        ]

        try:
            completed = subprocess.run(
                cmd,
                cwd=project_path,
                env=self._child_env(),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise ExtractorMissingError(
                f"Could not execute PHP binary {php!r}: {e}",
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ExtractorTimeoutError(
                f"Extraction timed out after {self._timeout}s.",
                stdout=e.stdout.decode("utf-8", errors="replace") if e.stdout else None,
                stderr=e.stderr.decode("utf-8", errors="replace") if e.stderr else None,
            ) from e

        self._check_command_known(completed.stdout, completed.stderr, completed.returncode)

        if completed.returncode != _EXIT_OK:
            # Phase 1 may still have written a partial document via
            # its fatal-error shutdown handler. We surface the non-zero
            # exit as a failure but keep the partial output around for
            # inspection.
            if host_tmp is not None:
                host_tmp.unlink(missing_ok=True)
            raise ExtractorFailedError(
                self._summarise_failure(completed.returncode, completed.stderr, completed.stdout),
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
            )

        # Move the container-written temp file to the real output location.
        if host_tmp is not None:
            if not host_tmp.is_file():
                raise ExtractorFailedError(
                    f"Extractor exited 0 but no output file was written at {host_tmp}.",
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    exit_code=completed.returncode,
                )
            # PHP inside the container wrote container paths everywhere in the
            # JSON (e.g. "/var/www/app/Models/User.php").  Rewrite them to host
            # paths so the rest of the pipeline (chunker, file readers) can open
            # the files.  We use a plain string replace because the container
            # path prefix is unique enough not to collide with content strings.
            assert container is not None  # implied by host_tmp is not None
            content = host_tmp.read_text(encoding="utf-8")
            container_prefix = str(container).rstrip("/")
            host_prefix = str(project_path).rstrip("/")
            content = content.replace(container_prefix, host_prefix)
            # Delete any prior output_path (a previous run may have left it
            # owned by the container's UID, blocking a plain write).  Unlink
            # only needs write on the parent dir, which we own.
            output_path.unlink(missing_ok=True)
            output_path.write_text(content, encoding="utf-8")
            host_tmp.unlink(missing_ok=True)
        elif not output_path.is_file():
            raise ExtractorFailedError(
                f"Extractor exited 0 but no output file was written at {output_path}.",
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
            )

        return ExtractorResult(
            output_path=output_path,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_php_binary(self) -> str:
        if self._php is not None:
            return self._php
        found = shutil.which("php")
        if found is None:
            raise ExtractorMissingError(
                "No 'php' binary found on PATH. Install PHP 8.2+ or pass "
                "php_binary=... to PhpExtractor.",
            )
        return found

    @staticmethod
    def _child_env() -> dict[str, str]:
        # Start from the current environment so PATH, HOME, and
        # LARAVEL_* variables survive, then scrub out a few variables
        # that would make runs non-reproducible.
        env = dict(os.environ)
        env.pop("PHP_IDE_CONFIG", None)
        env.pop("XDEBUG_SESSION", None)
        return env

    @staticmethod
    def _check_command_known(stdout: str, stderr: str, exit_code: int) -> None:
        """Detect the "command not defined" error and remap to ExtractorMissingError.

        Laravel's Artisan prints "There are no commands defined in the
        'nexus' namespace" (or "Command … is not defined") when the
        user's project doesn't have the Composer package installed.
        Artisan writes this to *stdout* in most Laravel versions, not
        stderr, so we check both streams.
        """
        if exit_code not in (_EXIT_USAGE_ERROR, _EXIT_FATAL_DEFAULT):
            return
        combined = (stdout + stderr).lower()
        if ("nexus" in combined and "no commands" in combined) or (
            "nexus:extract" in combined and ("not defined" in combined or "not found" in combined)
        ):
            raise ExtractorMissingError(
                "The 'nexus:extract' Artisan command is not registered in this "
                "project. Install the Composer package with: "
                "composer require --dev nick-nds/nexus-extractor",
                stderr=stderr,
                exit_code=exit_code,
            )

    @staticmethod
    def _summarise_failure(exit_code: int, stderr: str, stdout: str = "") -> str:
        # Artisan writes errors to stdout in most Laravel versions; show whichever
        # stream has content, preferring stderr so we don't bury real stack traces.
        primary, label = (stderr, "stderr") if stderr.strip() else (stdout, "stdout")
        trimmed = primary.strip().splitlines()[-20:] if primary else []
        tail = "\n".join(trimmed)
        return (
            f"Extractor exited {exit_code}. Last {label} lines:\n{tail}"
            if tail
            else f"Extractor exited {exit_code} with no output."
        )
