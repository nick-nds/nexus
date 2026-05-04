"""Typed exceptions for the embedder adapters.

Separate error classes let the pipeline layer (and Phase 5's CLI) map
each failure mode to a specific remediation message. "Ollama daemon
down" and "model not pulled" both appear as generic
``EmbedderRequestError`` without this split; with it, the user sees
the exact fix.
"""

from __future__ import annotations


class EmbedderError(Exception):
    """Base class for every embedder adapter failure."""


class EmbedderConnectionError(EmbedderError):
    """The embedder backend is unreachable.

    Typically "Ollama daemon isn't running" or "firewall blocks the
    port". The message carries the exact host and a copy-pasteable
    remediation.
    """


class EmbedderModelNotFoundError(EmbedderError):
    """The backend is running but the requested model isn't installed."""


class EmbedderRequestError(EmbedderError):
    """Any other API-level failure from the embedder backend."""
