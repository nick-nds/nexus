"""LSP enrichment in the post-extraction (package) pipeline.

Package indexes historically could never carry ``CALLS`` edges: the
post-extraction pipeline omitted ``EnrichWithLspPass`` outright, so
``find_callers`` on a package always reported ``calls_not_indexed``.
That is correct for Nexus-driven mode (the scratch Testbench tree is
transient), but not for in-repo mode, where the package is a real
checkout on disk an LSP can index. The pipeline therefore takes an
explicit opt-in.
"""

from __future__ import annotations

from nexus.pipeline.factory import build_post_extraction_pipeline


def _pass_names(pipeline: object) -> list[str]:
    return [type(p).__name__ for p in pipeline.passes]  # type: ignore[attr-defined]


def test_post_extraction_pipeline_omits_lsp_by_default() -> None:
    names = _pass_names(build_post_extraction_pipeline())

    assert "EnrichWithLspPass" not in names
    assert names == ["BuildGraphPass", "ChunkPass", "EmbedAndPersistPass"]


def test_post_extraction_pipeline_includes_lsp_when_opted_in() -> None:
    names = _pass_names(build_post_extraction_pipeline(include_lsp=True))

    assert "EnrichWithLspPass" in names
    # CALLS enrichment must run after the graph exists and before chunking.
    assert names.index("BuildGraphPass") < names.index("EnrichWithLspPass")
    assert names.index("EnrichWithLspPass") < names.index("ChunkPass")
