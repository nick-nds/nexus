"""Error-path tests for RunExtractorPass.

The integration test exercises the happy path; these unit tests use
stub extractors to cover every structured-error branch so the
pipeline's user-facing error messages are locked in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from nexus.adapters.extractor.errors import (
    ExtractorError,
    ExtractorFailedError,
    ExtractorMissingError,
    ExtractorTimeoutError,
)
from nexus.adapters.extractor.php_subprocess import ExtractorResult
from nexus.adapters.storage import ProjectStorage
from nexus.pipeline.context import PipelineContext
from nexus.pipeline.passes.run_extractor import RunExtractorPass


@dataclass(frozen=True)
class _StubProfile:
    name: str = "stub"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


class _RaisingExtractor:
    """Stub extractor that raises a configured exception on every call."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def extract(self, project_path: Path, *, output_path: Path):
        raise self._exc


class _WritingExtractor:
    """Stub extractor that writes a file and returns ExtractorResult."""

    def __init__(self, contents: str | None) -> None:
        self._contents = contents

    def extract(self, project_path: Path, *, output_path: Path):
        if self._contents is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(self._contents)
        return ExtractorResult(
            output_path=output_path,
            exit_code=0,
            stdout="",
            stderr="",
        )


@pytest.fixture
def ctx(tmp_path: Path) -> PipelineContext:
    project = tmp_path / "project"
    project.mkdir()
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="test")
    return PipelineContext(
        project_path=project,
        storage=storage,
        profile=_StubProfile(),
    )


class TestErrorMapping:
    def test_missing_extractor_becomes_extractor_missing(self, ctx: PipelineContext) -> None:
        pass_ = RunExtractorPass(
            extractor=_RaisingExtractor(ExtractorMissingError("install it")),  # type: ignore[arg-type]
        )

        pass_.run(ctx)

        assert not ctx.ok()
        assert any(e.code == "extractor_missing" for e in ctx.errors)
        assert ctx.reflection is None

    def test_timeout_becomes_extractor_timeout(self, ctx: PipelineContext) -> None:
        pass_ = RunExtractorPass(
            extractor=_RaisingExtractor(ExtractorTimeoutError("slow")),  # type: ignore[arg-type]
        )

        pass_.run(ctx)

        assert not ctx.ok()
        assert any(e.code == "extractor_timeout" for e in ctx.errors)

    def test_failed_becomes_extractor_failed(self, ctx: PipelineContext) -> None:
        pass_ = RunExtractorPass(
            extractor=_RaisingExtractor(
                ExtractorFailedError("boom", stderr="stack", exit_code=1),
            ),  # type: ignore[arg-type]
        )

        pass_.run(ctx)

        errors = [e for e in ctx.errors if e.code == "extractor_failed"]
        assert len(errors) == 1
        assert errors[0].context["exit_code"] == 1
        assert "stack" in errors[0].context["stderr"]

    def test_unknown_extractor_error(self, ctx: PipelineContext) -> None:
        pass_ = RunExtractorPass(
            extractor=_RaisingExtractor(ExtractorError("weird")),  # type: ignore[arg-type]
        )

        pass_.run(ctx)

        assert any(e.code == "extractor_error" for e in ctx.errors)


class TestReflectionParsing:
    def test_version_mismatch_is_typed(self, ctx: PipelineContext) -> None:
        pass_ = RunExtractorPass(
            extractor=_WritingExtractor(json.dumps({"schema_version": "99.0.0"})),  # type: ignore[arg-type]
        )

        pass_.run(ctx)

        assert any(e.code == "reflection_version_mismatch" for e in ctx.errors)

    def test_malformed_json_becomes_parse_failed(self, ctx: PipelineContext) -> None:
        pass_ = RunExtractorPass(
            extractor=_WritingExtractor("{not json"),  # type: ignore[arg-type]
        )

        pass_.run(ctx)

        assert any(e.code == "reflection_parse_failed" for e in ctx.errors)
