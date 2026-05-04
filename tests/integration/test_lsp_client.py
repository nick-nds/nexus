"""Integration tests for :class:`nexus.adapters.lsp.LspClient`.

These spawn a real PHP language server (phpactor or intelephense)
against a tiny PHP fixture written into ``tmp_path``. If neither
server is on the host the tests skip; CI without an LSP server
still passes.

Timeout coverage uses a deliberately short ``request_timeout_seconds``
combined with a subprocess that doesn't speak LSP, so we can verify
``references`` returns ``[]`` instead of hanging.
"""

from __future__ import annotations

import shutil
import time
from typing import TYPE_CHECKING

import pytest
from nexus.adapters.lsp import LspClient, LspProtocolError, resolve_lsp_binary

if TYPE_CHECKING:
    from pathlib import Path

# Used in tests that need a working server.  Reuses the resolver so
# the test mirrors what production wiring will do.
_RESOLVED = resolve_lsp_binary()


@pytest.fixture
def fixture_workspace(tmp_path: Path) -> Path:
    """A two-file PHP workspace: ``App\\Foo::bar`` is called from ``Caller.php``."""
    foo = tmp_path / "Foo.php"
    foo.write_text(
        "<?php\n"
        "namespace App;\n"
        "\n"
        "class Foo\n"
        "{\n"
        "    public function bar(): int\n"
        "    {\n"
        "        return 42;\n"
        "    }\n"
        "}\n",
    )

    caller = tmp_path / "Caller.php"
    caller.write_text(
        "<?php\n$foo = new \\App\\Foo();\n$result = $foo->bar();\n",
    )

    composer = tmp_path / "composer.json"
    composer.write_text(
        '{"name": "nexus-test/lsp-fixture", "autoload": {"psr-4": {"App\\\\": ""}}}\n',
    )

    return tmp_path


@pytest.mark.skipif(_RESOLVED is None, reason="No LSP server (intelephense/phpactor) available.")
def test_references_returns_at_least_one_location(fixture_workspace: Path) -> None:
    """Querying references of ``Foo::bar`` finds the caller in ``Caller.php``."""
    assert _RESOLVED is not None  # narrow for mypy
    binary, args = _RESOLVED

    client = LspClient(binary, args, request_timeout_seconds=20.0)
    try:
        client.prepare(fixture_workspace)
        # Open both files so the server has them available even if
        # workspace auto-indexing is slow or disabled.
        for filename in ("Foo.php", "Caller.php"):
            file_path = fixture_workspace / filename
            client._send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file_path.resolve().as_uri(),
                        "languageId": "php",
                        "version": 1,
                        "text": file_path.read_text(),
                    },
                },
            )

        # Give the language server a moment to ingest the didOpen
        # notifications and run its initial type analysis.  A short
        # poll-with-retry trades determinism for tolerating servers
        # whose indexing latency varies with cold caches.
        results: list[object] = []
        for _ in range(15):
            results = list(
                client.references(
                    fixture_workspace / "Foo.php",
                    # Line 6, column 22 = the 'a' in 'bar' (the symbol
                    # spans columns 21-23 in the fixture, 1-indexed).
                    line=6,
                    character=22,
                ),
            )
            if results:
                break
            time.sleep(0.5)

        assert len(results) >= 1, f"Expected at least one reference to App\\Foo::bar, got {results}"
    finally:
        client.close()


def test_close_is_safe_when_prepare_was_never_called() -> None:
    """``close`` on a never-started client must not raise."""
    client = LspClient("/usr/bin/false", request_timeout_seconds=1.0)
    client.close()  # should be a no-op


def test_references_returns_empty_when_client_is_not_prepared(tmp_path: Path) -> None:
    """Without ``prepare`` the client returns an empty list."""
    client = LspClient("/usr/bin/false", request_timeout_seconds=1.0)

    assert client.references(tmp_path / "x.php", line=1, character=1) == []


def test_request_timeout_raises_during_prepare(tmp_path: Path) -> None:
    """A subprocess that never writes to stdout causes ``prepare`` to time out."""
    sleep_binary = shutil.which("sleep")
    if sleep_binary is None:
        pytest.skip("sleep not available on PATH")

    # ``sleep 60`` never reads stdin or writes stdout, so the
    # initialize request never gets a response.  The 0.5s timeout
    # surfaces as :class:`LspProtocolError` rather than a hang.
    client = LspClient(sleep_binary, args=("60",), request_timeout_seconds=0.5)
    with pytest.raises(LspProtocolError, match="timed out"):
        client.prepare(tmp_path)
    client.close()
