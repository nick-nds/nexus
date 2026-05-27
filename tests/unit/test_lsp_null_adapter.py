"""Unit tests for :class:`nexus.adapters.lsp.NullLsp`.

These cover the behavioural contract - every method returns the
documented empty result and never raises - plus a static type check
that ``NullLsp`` structurally satisfies the :class:`Lsp` protocol so
the pipeline can substitute it freely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.adapters.lsp import NullLsp

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.core.protocols import Lsp


def test_null_lsp_satisfies_protocol() -> None:
    """A ``NullLsp`` instance is assignable to the :class:`Lsp` protocol.

    This is checked at type-check time by mypy. The runtime call here
    is just a smoke test that nothing raises during construction.
    """
    lsp: Lsp = NullLsp()
    assert lsp is not None


def test_prepare_accepts_a_workspace_root_without_raising(tmp_path: Path) -> None:
    NullLsp().prepare(tmp_path)


def test_references_returns_empty_list(tmp_path: Path) -> None:
    file = tmp_path / "Some.php"

    result = NullLsp().references(file, line=10, character=4)

    assert result == []


def test_references_returns_empty_list_for_any_position(tmp_path: Path) -> None:
    file = tmp_path / "Other.php"
    lsp = NullLsp()

    assert lsp.references(file, line=1, character=1) == []
    assert lsp.references(file, line=999, character=999) == []


def test_close_is_a_no_op() -> None:
    NullLsp().close()


def test_close_is_idempotent() -> None:
    lsp = NullLsp()
    lsp.close()
    lsp.close()


def test_full_lifecycle_roundtrip(tmp_path: Path) -> None:
    """A typical usage sequence - prepare, reference, close - works end to end."""
    lsp = NullLsp()

    lsp.prepare(tmp_path)
    refs = lsp.references(tmp_path / "User.php", line=42, character=8)
    lsp.close()

    assert refs == []
