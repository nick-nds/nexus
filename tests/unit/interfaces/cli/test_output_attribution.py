"""CLI output includes attribution for package-kind projects.

Tests verify that render() injects a "package" key into JSON output
and appends a footer to pretty output — only for package-kind projects.
Project-kind output must be bit-for-bit identical to output without meta.
"""

from __future__ import annotations

import io
import json

from nexus.adapters.storage.project_storage import ProjectMeta
from nexus.core.query import ToolOutput
from nexus.core.reflection.document import PackageMetadata
from nexus.interfaces.cli.context import CliContext, OutputFormat
from nexus.interfaces.cli.output import render
from pydantic import Field
from rich.console import Console

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _StubOutput(ToolOutput):
    """Minimal ToolOutput fixture — no attribution fields."""

    total: int = 42
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)


def _package_meta() -> ProjectMeta:
    return ProjectMeta(
        project_slug="foo--bar",
        project_path="/x",
        kind="package",
        package=PackageMetadata(
            vendor="foo",
            name="bar",
            version="1.0",
            license="MIT",
        ),
    )


def _project_meta() -> ProjectMeta:
    return ProjectMeta(project_slug="my-app", project_path="/x")


def _json_ctx(tmp_path: object) -> CliContext:
    return CliContext(output_format=OutputFormat.JSON, color=False)


def _pretty_ctx(tmp_path: object) -> CliContext:
    return CliContext(output_format=OutputFormat.PRETTY, color=False)


def _capture_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, no_color=True, width=120)
    return console, buf


# ---------------------------------------------------------------------------
# JSON format — package-kind
# ---------------------------------------------------------------------------


class TestJsonAttributionPackageKind:
    def test_package_field_is_present_in_json_output(self) -> None:
        buf = io.StringIO()
        ctx = _json_ctx(None)
        output = _StubOutput()

        # Redirect stdout
        import sys

        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            render(ctx, output, meta=_package_meta())
        finally:
            sys.stdout = old_stdout

        data = json.loads(buf.getvalue())
        assert "package" in data
        assert data["package"]["vendor"] == "foo"
        assert data["package"]["name"] == "bar"
        assert data["package"]["version"] == "1.0"
        assert data["package"]["license"] == "MIT"

    def test_tool_result_fields_are_preserved_in_json_output(self) -> None:
        buf = io.StringIO()
        ctx = _json_ctx(None)
        output = _StubOutput()

        import sys

        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            render(ctx, output, meta=_package_meta())
        finally:
            sys.stdout = old_stdout

        data = json.loads(buf.getvalue())
        assert data["total"] == 42


# ---------------------------------------------------------------------------
# JSON format — project-kind (no attribution)
# ---------------------------------------------------------------------------


class TestJsonAttributionProjectKind:
    def test_no_package_field_for_project_kind(self) -> None:
        buf = io.StringIO()
        ctx = _json_ctx(None)
        output = _StubOutput()

        import sys

        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            render(ctx, output, meta=_project_meta())
        finally:
            sys.stdout = old_stdout

        data = json.loads(buf.getvalue())
        assert "package" not in data

    def test_no_package_field_when_meta_is_none(self) -> None:
        buf = io.StringIO()
        ctx = _json_ctx(None)
        output = _StubOutput()

        import sys

        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            render(ctx, output, meta=None)
        finally:
            sys.stdout = old_stdout

        data = json.loads(buf.getvalue())
        assert "package" not in data

    def test_json_output_unchanged_without_meta(self) -> None:
        """project-kind output must be identical to output with no meta at all."""
        buf_no_meta = io.StringIO()
        buf_project = io.StringIO()
        ctx = _json_ctx(None)
        output = _StubOutput()

        import sys

        old = sys.stdout

        sys.stdout = buf_no_meta
        try:
            render(ctx, output, meta=None)
        finally:
            sys.stdout = old

        sys.stdout = buf_project
        try:
            render(ctx, output, meta=_project_meta())
        finally:
            sys.stdout = old

        assert buf_no_meta.getvalue() == buf_project.getvalue()


# ---------------------------------------------------------------------------
# Pretty format — package-kind
# ---------------------------------------------------------------------------


class TestPrettyAttributionPackageKind:
    def test_footer_present_in_pretty_output(self) -> None:
        console, buf = _capture_console()
        ctx = _pretty_ctx(None)
        output = _StubOutput()

        render(ctx, output, console=console, meta=_package_meta())

        text = buf.getvalue()
        assert "Indexed from foo/bar@1.0" in text

    def test_license_in_footer(self) -> None:
        console, buf = _capture_console()
        ctx = _pretty_ctx(None)
        output = _StubOutput()

        render(ctx, output, console=console, meta=_package_meta())

        text = buf.getvalue()
        assert "MIT" in text


# ---------------------------------------------------------------------------
# Pretty format — project-kind (no footer)
# ---------------------------------------------------------------------------


class TestPrettyAttributionProjectKind:
    def test_no_footer_for_project_kind(self) -> None:
        console, buf = _capture_console()
        ctx = _pretty_ctx(None)
        output = _StubOutput()

        render(ctx, output, console=console, meta=_project_meta())

        text = buf.getvalue()
        assert "Indexed from" not in text

    def test_no_footer_when_meta_is_none(self) -> None:
        console, buf = _capture_console()
        ctx = _pretty_ctx(None)
        output = _StubOutput()

        render(ctx, output, console=console, meta=None)

        text = buf.getvalue()
        assert "Indexed from" not in text
