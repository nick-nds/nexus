"""Typed exceptions for the PHP extractor adapter.

Distinct error types let the pipeline layer (and Phase 5's CLI) map
each failure mode to a specific remediation message. "php not on
PATH" and "container failed to boot" both appear as
``ExtractorFailedError`` today but we want to distinguish them at
the call site without string-matching the message.
"""

from __future__ import annotations


class ExtractorError(Exception):
    """Base class for every extractor-adapter failure.

    Catch this in pipeline code when you want the "extraction didn't
    work for any reason" case without caring about the specific sub-
    kind. All subclasses preserve the original command's stdout and
    stderr for forensics.
    """

    def __init__(
        self,
        message: str,
        *,
        stdout: str | None = None,
        stderr: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class ExtractorMissingError(ExtractorError):
    """The ``php`` binary or ``nexus:extract`` Artisan command is missing.

    Phase 5's CLI maps this to a "please install nexus/extractor-php"
    remediation message.
    """


class ExtractorTimeoutError(ExtractorError):
    """The extractor command ran longer than the configured timeout.

    Raised when the subprocess wrapper's timeout expires. Callers
    typically retry with a higher timeout or abort the run.
    """


class ExtractorFailedError(ExtractorError):
    """The extractor exited non-zero for a reason we don't classify.

    Includes Laravel boot failures, write failures, malformed JSON
    output, and any other exit-1 path. The original stderr is attached
    so the user can see what went wrong without re-running.
    """
