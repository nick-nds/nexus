"""Generic JSON-RPC LSP client for any stdio-based language server.

Implements the :class:`~nexus.core.protocols.Lsp` protocol over a child
process speaking the Language Server Protocol on stdin/stdout. Tested
against ``intelephense --stdio`` and ``phpactor language-server``;
both follow the spec strictly enough for the same wire-format code to
work for either.

Threading
=========

LSP servers can interleave responses with notifications and requests
they initiate themselves. To keep the calling code simple, a
background reader thread drains the server's stdout into a queue.
The main thread sends a request, then dequeues messages until it
finds one with the matching ``id``.

Error handling
==============

* ``prepare`` raises if the subprocess can't start or the
  ``initialize`` handshake fails.
* ``references`` swallows timeouts and protocol errors and returns
  ``[]`` per the :class:`Lsp` contract.
* ``close`` is best-effort - it sends ``shutdown`` / ``exit`` and
  kills the process if it doesn't terminate within a short window.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

from nexus.core.lsp import FileLocation

if TYPE_CHECKING:
    from pathlib import Path


class LspProtocolError(Exception):
    """Raised when the LSP server returns a JSON-RPC error or unexpected payload."""


class LspClient:
    """An :class:`~nexus.core.protocols.Lsp` implementation over JSON-RPC stdio.

    A single instance manages one subprocess. Construct one per
    indexing run; call :meth:`prepare` before use and :meth:`close`
    after.
    """

    def __init__(
        self,
        binary: str,
        args: tuple[str, ...] = (),
        *,
        request_timeout_seconds: float = 30.0,
        shutdown_timeout_seconds: float = 2.0,
    ) -> None:
        """Configure the client.

        Args:
            binary: Absolute path to the LSP server executable.
            args: Additional CLI arguments passed to the binary,
                e.g. ``("--stdio",)`` for intelephense or
                ``("language-server",)`` for phpactor.
            request_timeout_seconds: How long to wait for a single
                request response before declaring it timed out and
                returning the empty fallback.
            shutdown_timeout_seconds: How long :meth:`close` waits
                for the subprocess to exit gracefully before killing
                it.
        """
        self._binary = binary
        self._args = args
        self._request_timeout = request_timeout_seconds
        self._shutdown_timeout = shutdown_timeout_seconds
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._messages: Queue[dict[str, Any] | None] = Queue()
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._workspace_root: Path | None = None
        # Files we've already pushed via ``textDocument/didOpen``.  Many LSP
        # servers (notably phpactor) only index a file once it's been
        # explicitly opened, so :meth:`references` lazy-opens each file
        # the first time it queries inside it.
        self._opened_files: set[Path] = set()

    # ------------------------------------------------------------------
    # Lsp protocol implementation
    # ------------------------------------------------------------------

    def prepare(self, workspace_root: Path) -> None:
        """Spawn the subprocess and complete the LSP initialise handshake.

        Idempotent: a second call against the same workspace is a no-op.
        Raises :class:`LspProtocolError` if the server can't be started
        or rejects the initialise request.
        """
        if self._proc is not None:
            return

        self._workspace_root = workspace_root
        try:
            self._proc = subprocess.Popen(
                [self._binary, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(workspace_root),
            )
        except OSError as e:
            raise LspProtocolError(
                f"Failed to start LSP server {self._binary!r}: {e}",
            ) from e

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        try:
            self._send_request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": workspace_root.resolve().as_uri(),
                    "capabilities": {},
                },
            )
        except LspProtocolError:
            self.close()
            raise

        self._send_notification("initialized", {})

    def references(
        self,
        file: Path,
        line: int,
        character: int,
    ) -> list[FileLocation]:
        """Return references to the symbol at the 1-indexed position.

        Returns an empty list if the server is not running, the call
        times out, or the server returns no locations. The first time
        we see a given file, it's pushed to the server via
        ``textDocument/didOpen`` so the LSP can index it; subsequent
        calls reuse that open.
        """
        if self._proc is None:
            return []

        resolved = file.resolve()
        self._ensure_did_open(resolved)

        try:
            result = self._send_request(
                "textDocument/references",
                {
                    "textDocument": {"uri": resolved.as_uri()},
                    "position": {
                        "line": line - 1,  # 1-indexed → 0-indexed for the wire
                        "character": character - 1,
                    },
                    "context": {"includeDeclaration": False},
                },
            )
        except LspProtocolError:
            return []

        if not isinstance(result, list):
            return []

        return [_decode_location(loc) for loc in result if _is_location(loc)]

    def _ensure_did_open(self, file: Path) -> None:
        """Send ``textDocument/didOpen`` once per file.

        Phpactor (and some other servers) only index a file once it's
        been explicitly opened. We read the file ourselves and push
        its contents to the server. Failures (unreadable file, broken
        pipe) are swallowed - :meth:`references` will then return an
        empty list, which is the documented contract.
        """
        if file in self._opened_files:
            return
        self._opened_files.add(file)
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        with contextlib.suppress(LspProtocolError, BrokenPipeError, OSError):
            self._send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": file.as_uri(),
                        "languageId": "php",
                        "version": 1,
                        "text": text,
                    },
                },
            )

    def close(self) -> None:
        """Shut the LSP server down. Idempotent."""
        if self._proc is None:
            return

        proc = self._proc
        self._proc = None
        with contextlib.suppress(BrokenPipeError, LspProtocolError, OSError):
            # ``_send_notification`` checks ``self._proc`` (now None) and would
            # raise ``LspProtocolError``; skip it here and write directly.
            body = json.dumps({"jsonrpc": "2.0", "method": "exit", "params": {}}).encode(
                "utf-8",
            )
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            assert proc.stdin is not None
            proc.stdin.write(header + body)
            proc.stdin.flush()

        try:
            proc.wait(timeout=self._shutdown_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        # Close pipes explicitly so the test runner doesn't see a
        # ``ResourceWarning: unclosed file`` when the Popen object is
        # garbage-collected.
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None

        # Reset the per-file open cache so a re-:meth:`prepare` against a
        # fresh subprocess starts clean.
        self._opened_files.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: dict[str, Any]) -> Any:
        if self._proc is None:
            raise LspProtocolError("LSP client is not prepared.")

        with self._send_lock:
            request_id = self._next_id
            self._next_id += 1
            self._write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
            )

        deadline = time.monotonic() + self._request_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LspProtocolError(f"LSP {method!r} request timed out.")
            try:
                msg = self._messages.get(timeout=remaining)
            except Empty:
                raise LspProtocolError(  # noqa: B904
                    f"LSP {method!r} request timed out.",
                )
            if msg is None:
                raise LspProtocolError(
                    f"LSP server closed connection during {method!r}.",
                )
            if msg.get("id") == request_id:
                if "error" in msg:
                    raise LspProtocolError(
                        f"LSP {method!r} error: {msg['error']}",
                    )
                return msg.get("result")
            # else: server-initiated notification or request, ignore.

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        with self._send_lock:
            self._write_message(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                },
            )

    def _write_message(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise LspProtocolError("LSP subprocess has no stdin.")
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _read_loop(self) -> None:
        try:
            while True:
                msg = self._read_one_message()
                if msg is None:
                    break
                self._messages.put(msg)
        finally:
            self._messages.put(None)

    def _read_one_message(self) -> dict[str, Any] | None:
        if self._proc is None or self._proc.stdout is None:
            return None
        stdout = self._proc.stdout
        headers = _read_frame_headers(stdout)
        if headers is None:
            return None
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return None
        body = stdout.read(length)
        if not body:
            return None
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


def _read_frame_headers(stdout: Any) -> dict[str, str] | None:
    """Read JSON-RPC frame headers from a binary stream until the blank-line separator.

    Returns ``None`` on EOF; otherwise the parsed lower-cased header
    map. The caller uses the ``content-length`` entry to size the
    body read that follows.
    """
    headers: dict[str, str] = {}
    while True:
        line = stdout.readline()
        if not line:
            return None
        decoded = line.decode("ascii").rstrip("\r\n")
        if not decoded:
            return headers
        key, _, value = decoded.partition(":")
        headers[key.strip().lower()] = value.strip()


def _is_location(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("uri"), str)
        and isinstance(value.get("range"), dict)
    )


def _decode_location(loc: dict[str, Any]) -> FileLocation:
    """Turn an LSP ``Location`` dict into a 1-indexed :class:`FileLocation`."""
    from pathlib import Path  # noqa: PLC0415
    from urllib.parse import unquote, urlparse  # noqa: PLC0415

    uri: str = loc["uri"]
    parsed = urlparse(uri)
    file_path = Path(unquote(parsed.path))

    rng = loc["range"]
    start = rng["start"]
    end = rng["end"]
    return FileLocation(
        file=file_path,
        start_line=int(start["line"]) + 1,
        start_character=int(start["character"]) + 1,
        end_line=int(end["line"]) + 1,
        end_character=int(end["character"]) + 1,
    )
