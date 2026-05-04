"""Pure unit tests for the LSP location decoder.

The decoder is pure data transformation: take a JSON-RPC ``Location``
dict and produce a 1-indexed :class:`FileLocation`. We test it
directly so the framing-and-subprocess tests can stay focused on
lifecycle.
"""

from __future__ import annotations

from pathlib import Path

from nexus.adapters.lsp.lsp_client import _decode_location, _is_location


def test_decode_converts_lsp_zero_indexed_position_to_one_indexed() -> None:
    location = {
        "uri": "file:///home/user/project/App.php",
        "range": {
            "start": {"line": 9, "character": 4},
            "end": {"line": 9, "character": 14},
        },
    }

    decoded = _decode_location(location)

    assert decoded.file == Path("/home/user/project/App.php")
    assert decoded.start_line == 10
    assert decoded.start_character == 5
    assert decoded.end_line == 10
    assert decoded.end_character == 15


def test_decode_handles_uri_percent_encoding() -> None:
    location = {
        "uri": "file:///home/user/My%20Project/App.php",
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 5},
        },
    }

    decoded = _decode_location(location)

    assert decoded.file == Path("/home/user/My Project/App.php")


def test_is_location_accepts_valid_payload() -> None:
    assert _is_location(
        {
            "uri": "file:///x.php",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
        },
    )


def test_is_location_rejects_missing_fields() -> None:
    assert not _is_location({"uri": "file:///x.php"})
    assert not _is_location({"range": {}})
    assert not _is_location({})
    assert not _is_location("string")
    assert not _is_location(None)


def test_is_location_rejects_non_string_uri() -> None:
    assert not _is_location({"uri": 42, "range": {}})
