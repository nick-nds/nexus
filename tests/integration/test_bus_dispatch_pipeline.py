"""End-to-end pipeline test for CQRS bus-dispatch call resolution.

Proves the whole vertical slice with the *real* ``GraphBuilder``: a
reflection document carrying a ``bus_dispatch`` finding (as the PHP
``BusDispatchVisitor`` would emit) plus the handler and dispatcher
classes is built into a graph, and ``find_callers`` on the handler's
``handle`` method returns the dispatch site - the exact query that was
blind before this feature.

The message and handler live in different sub-namespaces (as they do in
real DDD/CQRS layouts), so this also exercises short-name handler
resolution through the builder rather than in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from nexus.core.graph.builder import GraphBuilder
from nexus.core.graph.ids import method_id
from nexus.core.graph.types import EdgeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.coverage import Coverage
from nexus.core.query.tools.find_callers import FindCallersInput, FindCallersTool
from nexus.core.reflection.document import (
    ClassEntry,
    ClassesSection,
    ClassReflection,
    MethodInfo,
    ProjectMetadata,
    ReflectionDocument,
    ReflectionSections,
    ReflectionSummary,
    StaticAnalysisFinding,
    StaticAnalysisSection,
)

MESSAGE = "App\\Modules\\Routing\\Application\\Queries\\EvaluateRoutingContextQuery"
HANDLER = "App\\Modules\\Routing\\Application\\QueryHandlers\\EvaluateRoutingContextQueryHandler"
DISPATCHER = "App\\Modules\\Routing\\Presentation\\RoutingController"


@dataclass(frozen=True)
class _Profile:
    name: str = "laravel-ddd-cqrs"
    custom_bases: dict[str, str] = field(default_factory=dict)
    custom_suffixes: dict[str, str] = field(default_factory=dict)


def _method(name: str) -> MethodInfo:
    return MethodInfo(
        name=name,
        visibility="public",
        static=False,
        abstract=False,
        final=False,
        parameters=[],
        line=10,
    )


def _class(fqn: str, kinds: list[str], methods: list[str]) -> ClassEntry:
    short = fqn.rsplit("\\", 1)[-1]
    namespace = fqn.rsplit("\\", 1)[0]
    return ClassEntry(
        source="project",
        kinds=kinds,
        reflection=ClassReflection(
            name=fqn,
            short_name=short,
            namespace=namespace,
            file=f"/app/{short}.php",
            abstract=False,
            final=False,
            methods=[_method(m) for m in methods],
        ),
    )


def _document() -> ReflectionDocument:
    classes = ClassesSection(
        count=3,
        items=[
            _class(MESSAGE, ["query"], ["__construct"]),
            _class(HANDLER, ["query_handler"], ["handle"]),
            _class(DISPATCHER, ["controller"], ["resolve"]),
        ],
    )
    dispatcher_file = f"/app/{DISPATCHER.rsplit(chr(92), 1)[-1]}.php"
    static = StaticAnalysisSection(
        file_count=1,
        finding_count=1,
        by_kind={"bus_dispatch": 1},
        findings=[
            StaticAnalysisFinding(
                kind="bus_dispatch",
                target=MESSAGE,
                in_class=DISPATCHER,
                in_method="resolve",
                file=dispatcher_file,
                line=55,
                meta={"method": "ask"},
            ),
        ],
    )
    return ReflectionDocument(
        schema_version="2.6.0",
        generated_at="2026-07-03T00:00:00+00:00",
        project=ProjectMetadata(
            name="Demo",
            environment="testing",
            laravel_version="12.0.0",
            php_version="8.3.0",
            base_path="/app",
        ),
        sections=ReflectionSections(classes=classes, static_analysis=static),
        summary=ReflectionSummary(
            sections=["classes", "static_analysis"],
            warning_count=0,
            error_count=0,
        ),
    )


def _ctx(graph: object) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    # No LSP: this is the case where only the bus-convention edge exists.
    return QueryContext(
        storage=storage,
        budget=ResponseBudget(),
        coverage=Coverage(calls_indexed=False),
    )


def test_bus_dispatch_is_traceable_end_to_end_without_an_lsp() -> None:
    outcome = GraphBuilder().build(_document(), _Profile())
    graph = outcome.value

    # The builder synthesised a CALLS edge from the dispatch site to the
    # handler's handle method, tagged as convention-derived.
    calls = [e for e in graph.edges if e.kind == EdgeKind.CALLS]
    assert len(calls) == 1
    assert calls[0].source == method_id(DISPATCHER, "resolve")
    assert calls[0].target == method_id(HANDLER, "handle")
    assert calls[0].attributes["via"] == "bus_convention"

    # find_callers now answers the previously-blind question, even though
    # no LSP ran (calls_indexed is False).
    output = FindCallersTool().execute(
        FindCallersInput(method_fqn=f"{HANDLER}::handle"),
        _ctx(graph),
    )
    assert output.error_code is None
    assert output.total == 1
    assert output.callers[0].class_fqn == DISPATCHER
    assert output.callers[0].method == "resolve"
    assert output.callers[0].line == 55
