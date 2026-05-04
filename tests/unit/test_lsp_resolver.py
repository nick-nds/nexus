"""Unit tests for :func:`nexus.adapters.lsp.resolve_lsp_binary`.

The resolver is pure aside from filesystem and ``$PATH`` checks. We
monkeypatch ``shutil.which`` and stub the Mason directory via
``Path.home`` so the tests don't depend on what's actually installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.adapters.lsp import resolve_lsp_binary

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_returns_none_when_no_server_is_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    assert resolve_lsp_binary() is None


def test_finds_intelephense_on_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def which(name: str) -> str | None:
        return "/usr/local/bin/intelephense" if name == "intelephense" else None

    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    result = resolve_lsp_binary()

    assert result == ("/usr/local/bin/intelephense", ("--stdio",))


def test_falls_back_to_phpactor_when_no_intelephense(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def which(name: str) -> str | None:
        return "/usr/local/bin/phpactor" if name == "phpactor" else None

    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    result = resolve_lsp_binary()

    assert result == ("/usr/local/bin/phpactor", ("language-server",))


def test_falls_back_to_mason_directory_when_path_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    mason_bin = tmp_path / ".local" / "share" / "nvim" / "mason" / "bin"
    mason_bin.mkdir(parents=True)
    phpactor = mason_bin / "phpactor"
    phpactor.write_text("#!/usr/bin/env bash\n")

    result = resolve_lsp_binary()

    assert result == (str(phpactor), ("language-server",))


def test_preferred_absolute_path_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit absolute path wins over both PATH and Mason."""
    monkeypatch.setattr("shutil.which", lambda _name: "/path-fallback")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    custom = tmp_path / "intelephense"
    custom.write_text("#!/usr/bin/env bash\n")

    result = resolve_lsp_binary(preferred=str(custom))

    assert result == (str(custom), ("--stdio",))


def test_preferred_unknown_binary_uses_no_extra_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A user-supplied custom server is still callable, just with no canned args."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    custom = tmp_path / "my-lsp-server"
    custom.write_text("#!/usr/bin/env bash\n")

    result = resolve_lsp_binary(preferred=str(custom))

    assert result == (str(custom), ())


def test_preferred_intelephense_via_shutil_which(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def which(name: str) -> str | None:
        return "/some/path/intelephense" if name == "intelephense" else None

    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    result = resolve_lsp_binary(preferred="intelephense")

    assert result == ("/some/path/intelephense", ("--stdio",))
