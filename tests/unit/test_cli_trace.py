"""Tests for ``nexus trace inspect`` (Gap #1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.commands.trace import (
    _find_latest_trace,
    inspect_command,
)
from nexus.interfaces.cli.context import CliContext, OutputFormat


def _make_ctx(tmp_path: Path, *, output: str = OutputFormat.JSON) -> CliContext:
    return CliContext(storage_root=tmp_path, output_format=output)


def _write_trace(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestInspectCommand:
    def test_inspect_pretty_summary_for_jsonl_path(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "t.jsonl"
        _write_trace(
            path,
            [
                {
                    "ts": "2026-05-04T10:00:00.000Z",
                    "trace_id": "abc",
                    "kind": "classifier_decision",
                    "query": "list routes",
                    "plan": {
                        "tool": "list_routes",
                        "args": {},
                        "confidence": 0.9,
                        "reason": "test",
                        "fallbacks": [],
                    },
                },
                {
                    "ts": "2026-05-04T10:00:00.010Z",
                    "trace_id": "abc",
                    "kind": "tool_executed",
                    "tool": "list_routes",
                    "args": {},
                    "duration_ms": 12.4,
                    "error_code": None,
                    "result_size": 5,
                    "over_budget": False,
                    "budget_ms": 200,
                },
                {
                    "ts": "2026-05-04T10:00:00.020Z",
                    "trace_id": "abc",
                    "kind": "ask_envelope",
                    "query": "list routes",
                    "final_tool": "list_routes",
                    "confidence": 0.9,
                    "reason": "test",
                    "alternatives_tried": [],
                },
            ],
        )

        ctx = _make_ctx(tmp_path)
        result = runner.invoke(inspect_command, [str(path)], obj=ctx)

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["query"] == "list routes"
        assert data["tool_calls_count"] == 1
        assert data["outcome"]["kind"] == "ask_envelope"
        assert data["trace_id"] == "abc"

    def test_inspect_missing_path_exits_1(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        ctx = _make_ctx(tmp_path)
        result = runner.invoke(inspect_command, [str(tmp_path / "nope.jsonl")], obj=ctx)

        assert result.exit_code == 1

    def test_inspect_empty_file_exits_1(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        ctx = _make_ctx(tmp_path)
        result = runner.invoke(inspect_command, [str(path)], obj=ctx)

        assert result.exit_code == 1

    def test_inspect_skips_malformed_lines(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "mixed.jsonl"
        path.write_text(
            "not-json\n"
            + json.dumps(
                {
                    "ts": "x",
                    "trace_id": "z",
                    "kind": "tool_executed",
                    "tool": "x",
                    "duration_ms": 1.0,
                    "error_code": None,
                    "result_size": 0,
                    "over_budget": False,
                    "budget_ms": 100,
                },
            )
            + "\n",
        )

        ctx = _make_ctx(tmp_path)
        result = runner.invoke(inspect_command, [str(path)], obj=ctx)

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["tool_calls_count"] == 1

    def test_path_and_last_are_mutually_exclusive(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "t.jsonl"
        path.write_text("{}\n")
        ctx = _make_ctx(tmp_path)
        result = runner.invoke(inspect_command, [str(path), "--last"], obj=ctx)

        assert result.exit_code == 2

    def test_neither_path_nor_last_exits_2(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        ctx = _make_ctx(tmp_path)
        result = runner.invoke(inspect_command, [], obj=ctx)
        assert result.exit_code == 2


class TestFindLatestTrace:
    def test_finds_most_recent_jsonl_under_traces_root(self, tmp_path: Path) -> None:
        traces_root = tmp_path / "traces" / "2026-05-04"
        old = traces_root / "old.jsonl"
        new = traces_root / "new.jsonl"
        traces_root.mkdir(parents=True)
        old.write_text("{}")
        new.write_text("{}")
        # Force ordering by mtime.
        import os

        os.utime(old, (1700000000, 1700000000))
        os.utime(new, (1800000000, 1800000000))

        latest = _find_latest_trace(tmp_path)
        assert latest == new

    def test_returns_none_when_no_traces_dir(self, tmp_path: Path) -> None:
        assert _find_latest_trace(tmp_path) is None

    def test_returns_none_when_traces_dir_empty(self, tmp_path: Path) -> None:
        (tmp_path / "traces").mkdir()
        assert _find_latest_trace(tmp_path) is None
