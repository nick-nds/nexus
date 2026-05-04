"""First pass: invoke the PHP extractor and parse its JSON output.

This is the only pass that touches the user's Laravel application.
On success the context carries a validated
:class:`~nexus.core.reflection.ReflectionDocument`. On failure the
context gets a typed error and the pipeline short-circuits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.adapters.extractor import (
    ExtractorError,
    ExtractorFailedError,
    ExtractorMissingError,
    ExtractorTimeoutError,
    PhpExtractor,
)
from nexus.core.outcome import Error
from nexus.core.reflection import (
    ReflectionLoadError,
    ReflectionVersionError,
    load_reflection,
)
from nexus.pipeline.progress import PassProgress

if TYPE_CHECKING:
    from nexus.pipeline.context import PipelineContext


class RunExtractorPass:
    """Run the PHP extractor and load the resulting document.

    The extractor adapter is injected so tests can substitute a
    fixture-backed stub. Production wiring uses the real
    :class:`PhpExtractor`.
    """

    name = "extract"

    def __init__(self, extractor: PhpExtractor | None = None) -> None:
        """Build a pass that drives ``extractor``.

        Args:
            extractor: The :class:`PhpExtractor` instance to invoke.
                Defaults to a plain :class:`PhpExtractor` with stock
                settings. Override for tests and for pipelines that
                need non-default timeout or ``php`` binary.
        """
        self._extractor = extractor or PhpExtractor()

    def run(self, ctx: PipelineContext) -> None:
        """Run the extractor and load its output into the context."""
        output = ctx.storage.reflection_path
        ctx.storage.initialise()

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=f"Running php artisan nexus:extract for {ctx.project_path.name}",
            ),
        )

        try:
            result = self._extractor.extract(ctx.project_path, output_path=output)
        except ExtractorMissingError as e:
            ctx.add_error(
                Error(
                    code="extractor_missing",
                    message=str(e),
                    context={"stderr": e.stderr or "", "exit_code": e.exit_code},
                ),
            )
            return
        except ExtractorTimeoutError as e:
            ctx.add_error(
                Error(
                    code="extractor_timeout",
                    message=str(e),
                    context={"stderr": e.stderr or ""},
                ),
            )
            return
        except ExtractorFailedError as e:
            ctx.add_error(
                Error(
                    code="extractor_failed",
                    message=str(e),
                    context={
                        "stderr": e.stderr or "",
                        "exit_code": e.exit_code,
                    },
                ),
            )
            return
        except ExtractorError as e:  # defensive catch-all
            ctx.add_error(
                Error(code="extractor_error", message=str(e)),
            )
            return

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=f"Loading reflection document from {result.output_path}",
            ),
        )

        try:
            ctx.reflection = load_reflection(result.output_path)
        except ReflectionVersionError as e:
            ctx.add_error(
                Error(
                    code="reflection_version_mismatch",
                    message=str(e),
                ),
            )
        except ReflectionLoadError as e:
            ctx.add_error(
                Error(
                    code="reflection_parse_failed",
                    message=str(e),
                ),
            )
