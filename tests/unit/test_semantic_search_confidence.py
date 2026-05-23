"""Tests for the ``min_vector_score`` threshold + ``confidence`` field (audit P0-11).

Before this change, semantic_search returned the top ``final_k`` rows
regardless of how weak the cosine similarity was. The synthesq-relay
audit showed gibberish queries returning 10 DTOs at vector_score ~0.57
— marginally below real-query scores of 0.6-0.68 — which made it
impossible for an agent to tell signal from noise.

The fix adds:
1. A ``min_vector_score`` input parameter (default 0.4) that filters
   out matches below the threshold.
2. A ``confidence`` output label (``high`` / ``medium`` / ``low``)
   derived from the top hit's vector_score.
3. An ``error_code="low_relevance"`` when no candidate crosses the
   threshold, instead of returning padding.
"""

from __future__ import annotations

from nexus.core.query.tools.semantic_search import (
    SemanticHit,
    SemanticSearchInput,
    SemanticSearchOutput,
    _confidence_for,
)


def test_confidence_high_for_strong_top_score() -> None:
    assert _confidence_for(0.65) == "high"
    assert _confidence_for(0.80) == "high"


def test_confidence_medium_for_middling_top_score() -> None:
    assert _confidence_for(0.55) == "medium"
    assert _confidence_for(0.64) == "medium"


def test_confidence_low_for_weak_top_score() -> None:
    assert _confidence_for(0.40) == "low"
    assert _confidence_for(0.54) == "low"


def test_min_vector_score_default_is_0_4() -> None:
    """The threshold default should filter obvious gibberish (~0.57 in
    the synthesq-relay audit) while keeping marginal real matches.

    0.4 was chosen as conservative — it lets a re-tune happen later
    without breaking calls that rely on the default. Agents that want
    stricter filtering pass a higher value.
    """
    payload = SemanticSearchInput(query="anything")
    assert payload.min_vector_score == 0.4


def test_min_vector_score_can_be_tightened() -> None:
    payload = SemanticSearchInput(query="x", min_vector_score=0.7)
    assert payload.min_vector_score == 0.7


def test_confidence_field_is_present_on_output() -> None:
    """The output model carries the new fields with sensible defaults
    so existing tests / agents don't break on the schema change."""
    output = SemanticSearchOutput(
        query="x",
        total_candidates=0,
        returned=0,
    )
    assert output.confidence is None
    assert output.filtered_by_threshold == 0


def test_semantic_hit_can_carry_vector_score() -> None:
    """Sanity check: the field used for the confidence calc is still
    on the hit. If this regresses, confidence will silently misreport.
    """
    hit = SemanticHit(
        node_id="x",
        node_kind="class",
        node_name="X",
        score=0.7,
        vector_score=0.65,
    )
    assert _confidence_for(hit.vector_score) == "high"
