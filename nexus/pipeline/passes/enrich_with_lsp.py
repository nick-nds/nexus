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

    In incremental mode (``ctx.changed_files`` is set and below the
    threshold), only methods in changed files are queried; CALLS edges
    targeting unchanged methods are carried forward from the previously
    persisted graph.
    """

    name = "enrich_with_lsp"

    #: How often the pass emits a progress event during the references
    #: scan.  One event every N method nodes processed.
    _PROGRESS_INTERVAL = 50

    #: When changed files exceed this fraction of all indexed files the
    #: pass falls back to full enrichment (the carry-forward bookkeeping
    #: would save nothing).
    _THRESHOLD = 0.5

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

        effective = self._effective_changed_files(ctx, method_nodes, file_for_class)

        if effective is None:
            self._enrich_full(ctx, method_nodes, file_for_class, methods_by_file)
        else:
            self._enrich_incremental(
                ctx, method_nodes, file_for_class, methods_by_file, effective,
            )

    # ------------------------------------------------------------------
    # Full enrichment (existing behaviour, extracted verbatim)
    # ------------------------------------------------------------------

    def _enrich_full(
        self,
        ctx: PipelineContext,
        method_nodes: list[Node],
        file_for_class: dict[str, Path],
        methods_by_file: dict[Path, list[tuple[int, Node]]],
    ) -> None:
        """Query LSP references for every method node."""
        assert ctx.lsp is not None
        assert ctx.graph is not None

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

    # ------------------------------------------------------------------
    # Threshold check
    # ------------------------------------------------------------------

    def _effective_changed_files(
        self,
        ctx: PipelineContext,
        method_nodes: list[Node],
        file_for_class: dict[str, Path],
    ) -> set[Path] | None:
        """Decide whether incremental mode is worthwhile.

        Returns the changed-file set to use, or ``None`` when the pass
        should fall back to full enrichment (either because
        ``ctx.changed_files`` is ``None`` or because the changed set
        exceeds the threshold).
        """
        if ctx.changed_files is None:
            return None

        all_files: set[Path] = set()
        for method in method_nodes:
            f = _file_for_method(method, file_for_class)
            if f is not None:
                all_files.add(f)

        if not all_files:
            return None

        changed_php_files = ctx.changed_files & all_files
        if len(changed_php_files) > len(all_files) * self._THRESHOLD:
            ctx.progress.emit(
                PassProgress(
                    pass_name=self.name,
                    message=(
                        f"Changed files ({len(changed_php_files)}) exceed "
                        f"{int(self._THRESHOLD * 100)}% of indexed files "
                        f"({len(all_files)}); using full enrichment."
                    ),
                ),
            )
            return None

        return ctx.changed_files

    # ------------------------------------------------------------------
    # Incremental enrichment
    # ------------------------------------------------------------------

    def _enrich_incremental(
        self,
        ctx: PipelineContext,
        method_nodes: list[Node],
        file_for_class: dict[str, Path],
        methods_by_file: dict[Path, list[tuple[int, Node]]],
        changed_files: set[Path],
    ) -> None:
        """Query LSP only for methods in changed files, carry forward the rest."""
        assert ctx.lsp is not None
        assert ctx.graph is not None

        # 1. Load old CALLS edges from persisted graph
        old_graph = ctx.storage.graph().load()
        old_calls_edges = [e for e in old_graph.edges if e.kind == EdgeKind.CALLS]

        # 2. Partition methods into query vs skip
        new_node_ids = {n.id for n in ctx.graph.nodes}
        methods_to_query: list[Node] = []
        skipped_ids: set[str] = set()

        for method in method_nodes:
            file = _file_for_method(method, file_for_class)
            if file is not None and file in changed_files:
                methods_to_query.append(method)
            else:
                skipped_ids.add(method.id)

        # 3. Carry forward old CALLS edges targeting skipped methods
        carried = 0
        for edge in old_calls_edges:
            if edge.target in skipped_ids and edge.source in new_node_ids:
                ctx.graph.add_edge(edge)
                carried += 1

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=(
                    f"Incremental: {len(methods_to_query)} methods to query, "
                    f"{len(skipped_ids)} unchanged (carried {carried} edges)"
                ),
                current=0,
                total=len(methods_to_query),
            ),
        )

        # 4. Query LSP only for methods in changed files
        ctx.lsp.prepare(ctx.project_path)
        edges_added = 0
        try:
            for index, method in enumerate(methods_to_query, start=1):
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
                                f"Queried LSP for {index} of "
                                f"{len(methods_to_query)} changed methods "
                                f"({edges_added} new + {carried} carried CALLS edges)"
                            ),
                            current=index,
                            total=len(methods_to_query),
                        ),
                    )
        finally:
            ctx.lsp.close()

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=(
                    f"Incremental done: {edges_added} fresh + {carried} carried = "
                    f"{edges_added + carried} total CALLS edges "
                    f"(queried {len(methods_to_query)} of {len(method_nodes)} methods)"
                ),
                detail={
                    "edges_added": edges_added,
                    "edges_carried": carried,
                    "methods_queried": len(methods_to_query),
                    "methods_skipped": len(skipped_ids),
                },
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
