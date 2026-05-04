"""Content-hash keyed embedding cache.

The cache sits between the pipeline and every :class:`Embedder`
backend. Its job is simple: never embed the same text twice for the
same model. On incremental sync this is the difference between a
5-second run and a 5-minute run.

Key design
==========

The cache key is ``sha256(model_id + ":" + text)``, with the model
id coming from the embedder so swapping models silently invalidates
the relevant entries (a new ``model_id`` produces a cold cache for
that model's entries — without wiping cached entries for other
models).

Layout
======

Cache files live under ``~/.nexus/cache/embeddings/<sanitised-model>/``.
Each vector is its own JSON file named by the hash hex digest. This
layout is:

* Simple — one file per entry means partial corruption is contained.
* Inspectable — users can ``cat`` an entry to see a vector.
* Easy to prune — ``nexus cache clear`` (Phase 5) is a ``rm -rf``.
* Cheap to write — no lock contention for concurrent processes.

At scale (the helm-v7 project has ~6000 chunks) this produces 6000
small files, which is fine for any modern filesystem. If that ever
becomes a problem we can shard by the first two hex characters of
the hash, but v1 keeps it flat.

The cache treats JSON parse errors as "miss" rather than propagating
the exception — a corrupted entry is not worth crashing the pipeline,
we just re-embed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 — runtime dataclass field type
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class EmbeddingCacheError(Exception):
    """Raised only for filesystem problems at the cache root.

    Corrupt individual entries are logged and skipped; they do not
    raise. We only raise when we can't even open the cache directory.
    """


@dataclass(slots=True)
class EmbeddingCache:
    """Directory-backed embedding cache keyed by ``(model_id, text)``.

    One instance is created per pipeline run. The directory layout is
    created lazily on the first write.
    """

    root: Path

    def __post_init__(self) -> None:
        """Create the cache root directory if it doesn't already exist."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise EmbeddingCacheError(f"Cannot create embedding cache at {self.root}: {e}") from e

    def directory_for(self, model_id: str) -> Path:
        """Return (and create) the per-model cache directory."""
        sub = self.root / _sanitise_model_id(model_id)
        sub.mkdir(parents=True, exist_ok=True)
        return sub

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, *, model_id: str, text: str) -> list[float] | None:
        """Return the cached vector for ``(model_id, text)`` or ``None``.

        A missing file, unreadable file, or corrupt JSON all map to
        ``None`` (cache miss). The pipeline then re-embeds.
        """
        path = self._entry_path(model_id, text)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, list):
            return None
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            return None

    def get_batch(
        self, *, model_id: str, texts: Iterable[str]
    ) -> tuple[dict[str, list[float]], list[str]]:
        """Look up multiple texts at once.

        Returns a tuple ``(hits, misses)`` where ``hits`` is a mapping
        from text to its cached vector and ``misses`` is the list of
        texts that weren't cached (in input order). The pipeline then
        calls the embedder with the misses and populates the cache
        before writing results.
        """
        hits: dict[str, list[float]] = {}
        misses: list[str] = []
        for text in texts:
            vector = self.get(model_id=model_id, text=text)
            if vector is None:
                misses.append(text)
            else:
                hits[text] = vector
        return hits, misses

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def put(self, *, model_id: str, text: str, vector: list[float]) -> None:
        """Persist a single vector to the cache.

        Writes to a sibling ``.tmp`` file and renames atomically so a
        crashing writer never leaves a half-written entry behind.
        """
        path = self._entry_path(model_id, text)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(vector), encoding="utf-8")
        tmp.replace(path)

    def put_batch(self, *, model_id: str, pairs: Mapping[str, list[float]]) -> None:
        """Persist multiple vectors in one call."""
        for text, vector in pairs.items():
            self.put(model_id=model_id, text=text, vector=vector)

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def clear(self, *, model_id: str | None = None) -> int:
        """Delete cached entries.

        Args:
            model_id: Restrict deletion to one model's subdirectory.
                ``None`` clears everything under the root.

        Returns:
            Number of files deleted.
        """
        deleted = 0
        if model_id is None:
            for entry in self.root.rglob("*.json"):
                if entry.is_file():
                    entry.unlink()
                    deleted += 1
            return deleted

        sub = self.directory_for(model_id)
        for entry in sub.glob("*.json"):
            entry.unlink()
            deleted += 1
        return deleted

    def size(self) -> int:
        """Return the total number of cached entries across all models."""
        return sum(1 for _ in self.root.rglob("*.json"))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _entry_path(self, model_id: str, text: str) -> Path:
        digest = hashlib.sha256(f"{model_id}:{text}".encode()).hexdigest()
        return self.directory_for(model_id) / f"{digest}.json"


_MODEL_ID_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitise_model_id(model_id: str) -> str:
    """Turn a model id into a filesystem-safe directory name.

    Model identifiers like ``fastembed:BAAI/bge-small-en-v1.5`` contain
    slashes and colons that aren't portable across filesystems. We
    replace anything that isn't ``[A-Za-z0-9_.-]`` with an underscore.
    """
    return _MODEL_ID_SAFE.sub("_", model_id)
