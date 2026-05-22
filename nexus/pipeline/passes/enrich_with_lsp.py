"""LSP-driven enrichment pass that produces ``CALLS`` edges.

Inserted between :class:`BuildGraphPass` and :class:`ChunkPass`. The
graph builder produces structural edges (routes, models, listeners…);
this pass adds the application-logic spine — who actually calls whom
— that an opinionated reflection extractor can't see.

The pass is a **no-op** when ``ctx.lsp`` is ``None``. The pipeline
factory should pass ``None`` whenever the host has no LSP server
available, so the pipeline still produces a structural graph; only
the ``CALLS`` enrichment is skipped.

Heuristics
==========

* The graph builder records each method's start ``line`` but not its
  ``end_line`` or column. We read each source file once to locate the
  method-name token on its declaration line, computing the column
  for the LSP query. The "enclosing method" of a returned reference
  is found by picking the method on the same file with the latest
  ``start_line`` ≤ the reference line. This is correct for non-nested
  PHP methods, which is the common case; a closure inside a method
  will be attributed to the enclosing method, which is the expected
  granularity here.
* Self-references are dropped (a method shouldn't have a CALLS edge
  to itself just because the LSP echoed the declaration site as a
  reference).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.outcome import Error, Warning
from nexus.pipeline.progress import PassProgress

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.pipeline.context import PipelineContext


class EnrichWithLspPass:
    """Add CALLS edges to the graph using LSP references.

    Skipped when ``ctx.lsp is None``. Adds non-fatal warnings for
    methods whose source file can't be read or whose declaration
    column can't be located; one such failure does not stop the run.
    """

    name = "enrich_with_lsp"

    #: How often the pass emits a progress event during the references
    #: scan.  One event every N method nodes processed.
    _PROGRESS_INTERVAL = 50

    def run(self, ctx: PipelineContext) -> None:
        """Walk method nodes, ask the LSP for references, add CALLS edges."""
        if ctx.graph is None:
            ctx.add_error(
                Error(
                    code="no_graph",
                    message=("EnrichWithLspPass needs a graph. Did BuildGraphPass run?"),
                ),
            )
            return

        if ctx.lsp is None:
            ctx.progress.emit(
                PassProgress(
                    pass_name=self.name,
                    message="No LSP configured; skipping CALLS enrichment.",
                ),
            )
            return

        method_nodes = [n for n in ctx.graph.nodes if n.kind == NodeKind.METHOD]
        if not method_nodes:
            ctx.progress.emit(
                PassProgress(
                    pass_name=self.name,
                    message="No method nodes in graph; nothing to enrich.",
                ),
            )
            return

        file_for_class: dict[str, Path] = _build_class_file_map(ctx.graph)
        methods_by_file: dict[Path, list[tuple[int, Node]]] = _index_methods_by_file(
            method_nodes,
            file_for_class,
        )

        ctx.lsp.prepare(ctx.project_path)
        edges_added = 0
        try:
            for index, method in enumerate(method_nodes, start=1):
                file = _file_for_method(method, file_for_class)
                line = _line_for_method(method)
                if file is None or line is None:
                    continue
                column = _find_symbol_column(file, line, method.name)
                if column is None:
                    ctx.add_warning(
                        Warning(
                            code="lsp_method_position_not_found",
                            message=(
                                f"Could not find symbol {method.name!r} on line {line} "
                                f"of {file}; skipping CALLS enrichment for this method."
                            ),
                            context={"method_id": method.id},
                        ),
                    )
                    continue

                refs = ctx.lsp.references(file, line, column)
                for ref in refs:
                    caller = _enclosing_method(methods_by_file, ref.file, ref.start_line)
                    if caller is None or caller.id == method.id:
                        continue
                    ctx.graph.add_edge(
                        Edge(
                            source=caller.id,
                            target=method.id,
                            kind=EdgeKind.CALLS,
                            attributes={
                                "file": str(ref.file),
                                "line": ref.start_line,
                                "character": ref.start_character,
                            },
                        ),
                    )
                    edges_added += 1

                if index % self._PROGRESS_INTERVAL == 0:
                    ctx.progress.emit(
                        PassProgress(
                            pass_name=self.name,
                            message=(
                                f"Queried LSP for {index} of {len(method_nodes)} methods "
                                f"({edges_added} CALLS edges so far)"
                            ),
                            current=index,
                            total=len(method_nodes),
                        ),
                    )
        finally:
            ctx.lsp.close()

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=(f"Added {edges_added} CALLS edges across {len(method_nodes)} methods"),
                detail={"edges_added": edges_added, "methods_scanned": len(method_nodes)},
            ),
        )


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _build_class_file_map(graph: Graph) -> dict[str, Path]:
    """Map class-node id → source file path.

    Method nodes only carry a ``class_fqn`` attribute and a start line;
    the class node carries the file. We pre-build the lookup so the
    main loop is O(1) per method.
    """
    mapping: dict[str, Path] = {}
    class_kinds = {
        NodeKind.CONTROLLER,
        NodeKind.MODEL,
        NodeKind.FORM_REQUEST,
        NodeKind.POLICY,
        NodeKind.MIDDLEWARE,
        NodeKind.OBSERVER,
        NodeKind.LISTENER,
        NodeKind.JOB,
        NodeKind.NOTIFICATION,
        NodeKind.MAILABLE,
        NodeKind.EVENT,
        NodeKind.RESOURCE,
        NodeKind.COMMAND,
        NodeKind.SERVICE_PROVIDER,
        NodeKind.CAST,
        NodeKind.CLASS,
    }
    for node in graph.nodes:
        if node.kind not in class_kinds:
            continue
        file = node.attributes.get("file")
        if isinstance(file, str):
            mapping[node.id] = Path(file)
    return mapping


def _index_methods_by_file(
    method_nodes: list[Node],
    file_for_class: dict[str, Path],
) -> dict[Path, list[tuple[int, Node]]]:
    """Group method nodes by the file their parent class lives in.

    The list for each file is sorted ascending by ``start_line`` so
    :func:`_enclosing_method` can do a single pass to find the latest
    method whose start line is ``<=`` a target.
    """
    out: dict[Path, list[tuple[int, Node]]] = {}
    for method in method_nodes:
        file = _file_for_method(method, file_for_class)
        line = _line_for_method(method)
        if file is None or line is None:
            continue
        out.setdefault(file, []).append((line, method))
    for entries in out.values():
        entries.sort(key=lambda pair: pair[0])
    return out


def _file_for_method(method: Node, file_for_class: dict[str, Path]) -> Path | None:
    class_fqn = method.attributes.get("class_fqn")
    if not isinstance(class_fqn, str):
        return None
    return file_for_class.get(f"class:{class_fqn}")


def _line_for_method(method: Node) -> int | None:
    line = method.attributes.get("line")
    if isinstance(line, int) and line > 0:
        return line
    return None


def _find_symbol_column(file: Path, line: int, symbol: str) -> int | None:
    """Return the 1-indexed column of ``symbol`` on ``line`` in ``file``.

    Returns ``None`` when the file can't be opened or the symbol isn't
    on the named line. Uses a simple substring search — false positives
    are rare for method names because the line is the declaration
    (e.g. ``    public function bar(...)``).
    """
    try:
        with file.open(encoding="utf-8", errors="replace") as fp:
            for current, text in enumerate(fp, start=1):
                if current == line:
                    pos = text.find(symbol)
                    return pos + 1 if pos >= 0 else None
                if current > line:
                    return None
    except OSError:
        return None
    return None


def _enclosing_method(
    methods_by_file: dict[Path, list[tuple[int, Node]]],
    file: Path,
    line: int,
) -> Node | None:
    """Pick the method node whose start line is the latest one ``<= line``.

    Linear scan. The list is pre-sorted by start line ascending; we
    walk it once and keep the most recent candidate. Good enough for
    typical PHP file sizes; if we ever index a 10k-line file we can
    swap in a binary search.
    """
    entries = methods_by_file.get(file)
    if not entries:
        return None
    best: Node | None = None
    for start_line, method in entries:
        if start_line > line:
            break
        best = method
    return best
