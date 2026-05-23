"""Typed exceptions for the query engine.

Two kinds of error warrant a real Python exception:

* :class:`ToolNotFoundError` — the caller asked for a tool name
  that's not in the registry. That's a caller bug or a stale
  cached tool list; either way, it's not a business "result".
* :class:`ToolInputError` — the caller passed input that doesn't
  validate against the tool's declared input schema. Phase 5's CLI
  and MCP server both parse the schema and construct inputs, so a
  validation failure here is a programmer error, not a user-facing
  condition.

Business-level "not found" results (unknown route, missing class)
are returned as typed output shapes, not exceptions.

Tool ``error_code`` taxonomy (audit P1-16)
==========================================

The query tools emit a stable ``error_code`` string on every
non-success response. The taxonomy was audited and codified after
the synthesq-relay MCP audit; see ``synthesq-relay-mcp-audit.md``
P1-16. The families:

* ``*_not_found`` — an exact lookup failed because the symbol isn't
  in the index. Emitted by ``describe_class``, ``find_callers``,
  ``find_dispatchers``, ``find_listeners``, ``get_policy_for``,
  ``resolve_binding``, ``trace_route``, etc. Includes
  ``class_not_found``, ``method_not_found``, ``event_not_found``,
  ``policy_not_found``, ``model_not_found``, ``route_not_found``,
  ``binding_not_found``, ``job_not_found``, ``key_not_found``,
  ``file_not_found``, ``node_not_found``.

* ``no_matches`` — *fuzzy* search returned zero hits. Distinct
  from ``*_not_found`` because the caller didn't specify an exact
  identifier. Emitted by ``explore_entity`` and ``describe_flow``.

* ``low_relevance`` — semantic search found candidates but all
  fell below the configured ``min_vector_score`` threshold. The
  caller can lower the threshold to inspect weak matches.

* ``invalid_*`` — input validation that's tool-specific (beyond
  Pydantic schema checks). ``invalid_direction``, ``invalid_kind``,
  ``invalid_mode``, ``invalid_range``.

* ``*_not_indexed`` — the index lacks the data a feature needs.
  ``calls_not_indexed`` (no LSP ran). Audit P0-8 introduced this
  family so graph tools don't silently return empty when the
  CALLS edges are missing.

* ``no_embedder`` / ``no_vector_dimensions`` — infrastructure not
  configured for semantic search. Emitted only by
  ``semantic_search`` when the query context lacks an embedder.

* ``non_listable_kind`` — ``list_by_kind`` was asked for a kind
  that has its own dedicated tool (routes, scheduled tasks). The
  error message points at the right tool.

* ``empty_module`` / ``missing_filter`` / ``key_not_found`` —
  tool-specific "the question was valid but the index has nothing
  to return" outcomes.

* ``file_outside_project`` / ``range_out_of_bounds`` / ``read_error``
  — emitted by body-retrieval tools (``get_full_block``,
  ``get_node_body``) for filesystem-side problems.

When in doubt, prefer a ``*_not_found`` code for exact-lookup misses
and ``no_matches`` for fuzzy searches. Don't invent new codes
without checking this list — and when adding one, append it here.
"""

from __future__ import annotations


class QueryEngineError(Exception):
    """Base class for query-engine failures."""


class ToolNotFoundError(QueryEngineError):
    """The requested tool name is not registered."""


class ToolInputError(QueryEngineError):
    """The tool's input failed validation."""
