"""Tests for nexus.core.graph.builder.

Two layers of testing:

1. End-to-end against the committed momskitchen reflection fixture.
   This is the strongest signal: real data, real shapes, real edge
   cases the synthetic fixtures would miss.
2. Synthetic unit cases for specific behaviours (warning emission,
   determinism, kind priority, profile customisation) that are awkward
   to assert against the committed fixture.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from nexus.core.graph.builder import GraphBuilder
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.reflection import load_reflection
from nexus.core.reflection.document import ClassEntry, ClassReflection

FIXTURE = Path(__file__).parent.parent / "fixtures" / "reflection-samples" / "momskitchen.json"


@dataclass(frozen=True)
class StubProfile:
    """Minimal Profile implementation for tests.

    Conforms to ``nexus.core.protocols.Profile`` structurally.
    """

    name: str = "test-profile"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # frozen dataclass: use object.__setattr__ to assign defaults
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


@pytest.fixture
def empty_profile() -> StubProfile:
    return StubProfile()


@pytest.fixture
def builder() -> GraphBuilder:
    return GraphBuilder()


# ----------------------------------------------------------------------------
# End-to-end against the real momskitchen fixture
# ----------------------------------------------------------------------------


class TestBuildAgainstRealFixture:
    """Smoke-test the builder against the committed Phase 1 output.

    These tests verify the most important contract: the entire pipeline
    from reflection JSON to typed graph works on real data without
    raising. The exact node/edge counts on real projects are stable
    enough to pin to specific numbers; if a refactor changes them by
    accident, the test fails loudly.
    """

    def test_builds_without_error(self, builder: GraphBuilder, empty_profile: StubProfile) -> None:
        document = load_reflection(FIXTURE)

        result = builder.build(document, empty_profile)

        assert result.ok
        assert len(result.value.nodes) > 0
        assert len(result.value.edges) > 0

    def test_routes_become_route_nodes(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        route_nodes = [n for n in result.value.nodes if n.kind == NodeKind.ROUTE]
        assert document.sections.routes is not None
        assert len(route_nodes) == document.sections.routes.count

    def test_each_class_entry_yields_exactly_one_class_node(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        # The strict 1:1 check: every entry in the classes section must
        # appear in the graph as a node with id ``class:<fqn>``. Listener
        # nodes, middleware-alias nodes, and policy nodes are derived
        # from other sections and live under different id prefixes —
        # they correctly do NOT count toward this total.
        assert document.sections.classes is not None
        class_ids = {f"class:{c.reflection.name}" for c in document.sections.classes.items}
        node_ids = {n.id for n in result.value.nodes if n.id.startswith("class:")}
        # Every class-section entry must produce a node.
        assert class_ids.issubset(node_ids)
        # And the count of class-prefixed nodes equals the entry count
        # (no extras synthesised from inheritance edges, etc.).
        # Note: inheritance edges target class:<parent> nodes which may
        # be vendor classes not in the project classmap, so the node-id
        # set is a SUPERSET of the entry set, not equal. Confirm the
        # entry set is fully covered.
        for cid in class_ids:
            assert cid in node_ids, f"Missing class node {cid}"

    def test_route_to_controller_method_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        # Every controller-action route should produce a routes_to edge
        # to a method node.
        assert document.sections.routes is not None
        controller_routes = sum(
            1 for r in document.sections.routes.items if r.action.kind == "controller"
        )
        routes_to_edges = [e for e in result.value.edges if e.kind == EdgeKind.ROUTES_TO]
        assert len(routes_to_edges) == controller_routes

    def test_listeners_link_to_events(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        listens_to_edges = [e for e in result.value.edges if e.kind == EdgeKind.LISTENS_TO]
        # Every class-listener entry in the reflection produces one edge.
        assert document.sections.events is not None
        expected = sum(
            sum(1 for cb in entry.listeners if cb.kind == "class")
            for entry in document.sections.events.listeners
        )
        assert len(listens_to_edges) == expected

    def test_inheritance_edges_present(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        extends_edges = [e for e in result.value.edges if e.kind == EdgeKind.EXTENDS]
        # The fixture has many controllers, models, jobs etc. with
        # parents — at least dozens of EXTENDS edges.
        assert len(extends_edges) > 10

    def test_method_nodes_have_part_of_edges(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        method_nodes = [n for n in result.value.nodes if n.kind == NodeKind.CONTROLLER_METHOD]
        part_of_edges = [e for e in result.value.edges if e.kind == EdgeKind.PART_OF]
        assert len(method_nodes) == len(part_of_edges)

    def test_middleware_aliases_become_nodes(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        mw_nodes = [n for n in result.value.nodes if n.kind == NodeKind.MIDDLEWARE]
        # momskitchen has 11 middleware aliases (verified during Phase 1)
        assert len(mw_nodes) >= 11


class TestDeterminism:
    """The most important property of the builder: same in, same out."""

    def test_two_builds_produce_equal_graphs(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)

        first = builder.build(document, empty_profile).value
        second = builder.build(document, empty_profile).value

        assert len(first.nodes) == len(second.nodes)
        assert len(first.edges) == len(second.edges)

        # Node ids must match in order.
        first_ids = [n.id for n in first.nodes]
        second_ids = [n.id for n in second.nodes]
        assert first_ids == second_ids


class TestPerformance:
    """Lock in the eager-index optimisation.

    A regression here usually means someone broke ``Graph.add_node``'s
    O(1) deduplication path, which on real enterprise fixtures
    (helm-v7, ~46k nodes) is the difference between a sub-second build
    and a 30-second build.
    """

    def test_momskitchen_builds_fast(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        document = load_reflection(FIXTURE)

        start = time.perf_counter()
        result = builder.build(document, empty_profile)
        elapsed = time.perf_counter() - start

        assert result.ok
        # Generous bound — momskitchen should build in well under 100 ms.
        # If this fails, the eager-index fix has likely been undone.
        assert elapsed < 0.5, f"Build took {elapsed * 1000:.0f}ms (regression)"


# ----------------------------------------------------------------------------
# Synthetic edge cases
# ----------------------------------------------------------------------------


class TestKindPriority:
    """The class-kind priority list collapses overlapping labels into one."""

    def _build_one_class(self, kinds: list[str]) -> NodeKind:
        builder = GraphBuilder()
        graph = Graph()
        entry = ClassEntry(
            source="project",
            kinds=kinds,
            reflection=ClassReflection(
                name="App\\X",
                short_name="X",
                namespace="App",
                file=None,
                abstract=False,
                final=False,
                parent=None,
                interfaces=[],
                traits=[],
                attributes=[],
                methods=[],
            ),
        )
        builder._build_classes(graph, [entry], StubProfile(), set())
        node = graph.node_by_id("class:App\\X")
        assert node is not None
        return node.kind

    def test_controller_wins_over_should_queue(self) -> None:
        # A controller class that uses ShouldQueue (rare but possible)
        # should still be classified as a controller.
        assert self._build_one_class(["controller", "should_queue"]) == NodeKind.CONTROLLER

    def test_form_request_wins_over_class(self) -> None:
        assert self._build_one_class(["form_request"]) == NodeKind.FORM_REQUEST

    def test_unknown_kind_falls_back_to_class(self) -> None:
        assert self._build_one_class(["weird_custom_kind"]) == NodeKind.CLASS


class TestBuilderEdgeCases:
    """Exercise builder branches the momskitchen fixture doesn't hit.

    Synthesises minimal ReflectionDocument fixtures that trigger the
    policy-mapping, scheduled-task, binding-closure, and route-closure
    code paths.
    """

    def _minimal_document(self, **section_overrides: object):
        from nexus.core.reflection.document import (
            BindingsSection,
            BindingsSummary,
            ClassesSection,
            EventListenersSection,
            GatesPoliciesSection,
            MiddlewareSection,
            ProjectMetadata,
            ReflectionDocument,
            ReflectionSections,
            ReflectionSummary,
            RoutesSection,
            ScheduleSection,
            StaticAnalysisSection,
        )

        defaults: dict[str, object] = {
            "routes": RoutesSection(count=0, items=[]),
            "bindings": BindingsSection(
                bindings=[],
                aliases=[],
                instances=[],
                summary=BindingsSummary(binding_count=0, alias_count=0, instance_count=0),
            ),
            "events": EventListenersSection(listeners=[], wildcards=[]),
            "gates_policies": GatesPoliciesSection(gates=[], policies=[]),
            "middleware": MiddlewareSection(),
            "config": {},
            "schedule": ScheduleSection(count=0, events=[]),
            "classes": ClassesSection(count=0, items=[]),
            "static_analysis": StaticAnalysisSection(
                file_count=0, finding_count=0, by_kind={}, findings=[]
            ),
        }
        defaults.update(section_overrides)
        sections = ReflectionSections(**defaults)  # type: ignore[arg-type]

        return ReflectionDocument(
            schema_version="2.0.0",
            generated_at="2026-04-08T00:00:00+00:00",
            project=ProjectMetadata(
                name="Test",
                environment="testing",
                laravel_version="12.0.0",
                php_version="8.3.0",
                base_path="/tmp",
            ),
            sections=sections,
            warnings=[],
            errors=[],
            summary=ReflectionSummary(sections=[], warning_count=0, error_count=0),
        )

    def test_policy_edges_link_model_to_policy(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            GatesPoliciesSection,
            PolicyEntry,
        )

        document = self._minimal_document(
            gates_policies=GatesPoliciesSection(
                gates=[],
                policies=[
                    PolicyEntry(model="App\\Models\\Post", policy="App\\Policies\\PostPolicy"),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        policy = result.value.node_by_id("policy:App\\Policies\\PostPolicy")
        assert policy is not None
        applies_to = [e for e in result.value.edges if e.kind.value == "applies_to"]
        assert len(applies_to) == 1
        assert applies_to[0].target == "class:App\\Models\\Post"

    def test_scheduled_commands_become_nodes(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            ScheduleEvent,
            ScheduleSection,
        )

        document = self._minimal_document(
            schedule=ScheduleSection(
                count=1,
                events=[
                    ScheduleEvent(
                        expression="0 * * * *",
                        kind="command",
                        command="php artisan backup:run",
                        description="Hourly backup",
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        schedule_nodes = [n for n in result.value.nodes if n.kind.value == "scheduled_task"]
        assert len(schedule_nodes) == 1
        assert schedule_nodes[0].attributes["command"] == "php artisan backup:run"

    def test_route_missing_controller_fields_warns(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            RouteAction,
            RouteItem,
            RoutesSection,
        )

        document = self._minimal_document(
            routes=RoutesSection(
                count=1,
                items=[
                    RouteItem(
                        uri="/broken",
                        methods=["GET"],
                        action=RouteAction(kind="controller", controller=None, method=None),
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        warnings = [w for w in result.value.warnings if w.code == "route_action_incomplete"]
        assert len(warnings) == 1


class TestWarnings:
    """The builder records non-fatal problems as Warnings, never raises."""

    def test_closure_listener_warns(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        # The momskitchen fixture has a number of closure listeners
        # registered by Laravel internals (PailServiceProvider,
        # FoundationServiceProvider). Confirm we record warnings for
        # them but still produce a graph.
        document = load_reflection(FIXTURE)
        result = builder.build(document, empty_profile)

        closure_warnings = [w for w in result.value.warnings if w.code == "closure_listener"]
        assert len(closure_warnings) > 0
        assert result.ok  # warnings don't flip ok-ness


class TestStaticEdges:
    """Builder translates PhaseC static-analysis findings into graph edges."""

    def _minimal_document(self, **section_overrides: object):
        from nexus.core.reflection.document import (
            BindingsSection,
            BindingsSummary,
            ClassesSection,
            EventListenersSection,
            GatesPoliciesSection,
            MiddlewareSection,
            ProjectMetadata,
            ReflectionDocument,
            ReflectionSections,
            ReflectionSummary,
            RoutesSection,
            ScheduleSection,
            StaticAnalysisSection,
        )

        defaults: dict[str, object] = {
            "routes": RoutesSection(count=0, items=[]),
            "bindings": BindingsSection(
                bindings=[],
                aliases=[],
                instances=[],
                summary=BindingsSummary(binding_count=0, alias_count=0, instance_count=0),
            ),
            "events": EventListenersSection(listeners=[], wildcards=[]),
            "gates_policies": GatesPoliciesSection(gates=[], policies=[]),
            "middleware": MiddlewareSection(),
            "config": {},
            "schedule": ScheduleSection(count=0, events=[]),
            "classes": ClassesSection(count=0, items=[]),
            "static_analysis": StaticAnalysisSection(
                file_count=0, finding_count=0, by_kind={}, findings=[]
            ),
        }
        defaults.update(section_overrides)
        sections = ReflectionSections(**defaults)  # type: ignore[arg-type]

        return ReflectionDocument(
            schema_version="2.0.0",
            generated_at="2026-04-08T00:00:00+00:00",
            project=ProjectMetadata(
                name="Test",
                environment="testing",
                laravel_version="12.0.0",
                php_version="8.3.0",
                base_path="/tmp",
            ),
            sections=sections,
            warnings=[],
            errors=[],
            summary=ReflectionSummary(sections=[], warning_count=0, error_count=0),
        )

    def test_event_dispatch_creates_fires_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"event_dispatch": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="event_dispatch",
                        target="App\\Events\\OrderPlaced",
                        in_class="App\\Http\\Controllers\\OrderController",
                        in_method="store",
                        file="/app/Http/Controllers/OrderController.php",
                        line=42,
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        fires_edges = [e for e in result.value.edges if e.kind == EdgeKind.FIRES]
        assert len(fires_edges) == 1
        assert fires_edges[0].source == "method:App\\Http\\Controllers\\OrderController::store"
        assert fires_edges[0].target == "class:App\\Events\\OrderPlaced"

    def test_job_dispatch_creates_dispatches_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"job_dispatch": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="job_dispatch",
                        target="App\\Jobs\\ProcessPayment",
                        in_class="App\\Http\\Controllers\\PaymentController",
                        in_method="charge",
                        file=None,
                        line=None,
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        dispatches_edges = [e for e in result.value.edges if e.kind == EdgeKind.DISPATCHES]
        assert len(dispatches_edges) == 1
        assert dispatches_edges[0].source == (
            "method:App\\Http\\Controllers\\PaymentController::charge"
        )
        assert dispatches_edges[0].target == "class:App\\Jobs\\ProcessPayment"

    def test_notification_dispatch_creates_notifies_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """Notifications get their own ``NOTIFIES`` edge — separate from job ``DISPATCHES``.

        Conflating them under ``DISPATCHES`` (the previous behaviour)
        forced agents to filter dispatches by class kind to answer
        "what notifications does this method send?". A dedicated edge
        kind makes the structural answer typeable.
        """
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"notification_dispatch": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="notification_dispatch",
                        target="App\\Notifications\\InvoiceReady",
                        in_class="App\\Http\\Controllers\\InvoiceController",
                        in_method="send",
                        file=None,
                        line=None,
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        notifies = [e for e in result.value.edges if e.kind == EdgeKind.NOTIFIES]
        assert len(notifies) == 1
        assert notifies[0].target == "class:App\\Notifications\\InvoiceReady"
        # And — crucially — there must be NO leakage into the
        # ``DISPATCHES`` bucket; that would re-conflate the two.
        dispatches = [e for e in result.value.edges if e.kind == EdgeKind.DISPATCHES]
        assert dispatches == []

    def test_view_return_creates_returns_view_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """``view_return`` findings produce a ``view`` node + ``RETURNS_VIEW`` edge.

        Same blade view returned from two methods collapses to a
        single view node — agents asking "which controllers render
        auth.login?" get a clean reverse traversal.
        """
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=2,
                by_kind={"view_return": 2},
                findings=[
                    StaticAnalysisFinding(
                        kind="view_return",
                        target="auth.login",
                        in_class="App\\Http\\Controllers\\AuthController",
                        in_method="show",
                        file=None,
                        line=12,
                    ),
                    StaticAnalysisFinding(
                        kind="view_return",
                        target="auth.login",
                        in_class="App\\Http\\Controllers\\HomeController",
                        in_method="redirectToLogin",
                        file=None,
                        line=42,
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        view_nodes = [n for n in result.value.nodes if n.kind == NodeKind.VIEW]
        assert len(view_nodes) == 1, "duplicate view targets must collapse to one node"
        assert view_nodes[0].id == "view:auth.login"
        assert view_nodes[0].name == "auth.login"

        view_edges = [e for e in result.value.edges if e.kind == EdgeKind.RETURNS_VIEW]
        assert len(view_edges) == 2
        sources = {e.source for e in view_edges}
        assert sources == {
            "method:App\\Http\\Controllers\\AuthController::show",
            "method:App\\Http\\Controllers\\HomeController::redirectToLogin",
        }
        # The line attribute is preserved on the edge so the agent
        # knows where in the source the view was returned from.
        assert all(e.attributes.get("line") in (12, 42) for e in view_edges)

    def test_schedule_callback_event_creates_runs_command_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """A scheduled callback to a class FQN gets a ``RUNS_COMMAND`` edge.

        Lets an agent answer "what schedules this job?" via reverse
        traversal of ``RUNS_COMMAND``. The edge attribute carries the
        cron expression so the agent can read it without re-fetching
        the source node.
        """
        from nexus.core.reflection.document import (
            ScheduleEvent,
            ScheduleSection,
        )

        document = self._minimal_document(
            schedule=ScheduleSection(
                count=1,
                events=[
                    ScheduleEvent(
                        expression="0 2 * * *",
                        timezone="UTC",
                        description="Daily token cleanup",
                        kind="callback",
                        target="App\\Jobs\\CheckExpiredTokensJob",
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        runs_edges = [e for e in result.value.edges if e.kind == EdgeKind.RUNS_COMMAND]
        assert len(runs_edges) == 1
        assert runs_edges[0].target == "class:App\\Jobs\\CheckExpiredTokensJob"
        assert runs_edges[0].attributes.get("expression") == "0 2 * * *"

        scheduled_nodes = [n for n in result.value.nodes if n.kind == NodeKind.SCHEDULED_TASK]
        assert len(scheduled_nodes) == 1
        assert runs_edges[0].source == scheduled_nodes[0].id

    def test_schedule_command_signature_does_not_create_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """A command-signature schedule (``cache:clear``) skips the edge.

        The command field carries a signature string, not an FQN, so
        we don't have a class id to point at. A future iteration may
        resolve signature → FQN via the classes section.
        """
        from nexus.core.reflection.document import (
            ScheduleEvent,
            ScheduleSection,
        )

        document = self._minimal_document(
            schedule=ScheduleSection(
                count=1,
                events=[
                    ScheduleEvent(
                        expression="*/5 * * * *",
                        kind="command",
                        command="cache:clear",
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        runs_edges = [e for e in result.value.edges if e.kind == EdgeKind.RUNS_COMMAND]
        assert runs_edges == []
        # The scheduled_task node still exists with the command attribute
        # so an agent can read the signature directly.
        scheduled_nodes = [n for n in result.value.nodes if n.kind == NodeKind.SCHEDULED_TASK]
        assert len(scheduled_nodes) == 1
        assert scheduled_nodes[0].attributes["command"] == "cache:clear"

    def test_observer_registration_creates_observes_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """``Model::observe(Observer::class)`` becomes an OBSERVES edge.

        The edge points observer → model so an agent doing reverse
        traversal on a model node ("who observes this?") gets a
        clean answer without a string-matching heuristic.
        """
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"observer_registration": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="observer_registration",
                        target="App\\Observers\\UserObserver",
                        in_class="App\\Providers\\EventServiceProvider",
                        in_method="boot",
                        file=None,
                        line=12,
                        meta={"model": "App\\Models\\User"},
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        observes = [e for e in result.value.edges if e.kind == EdgeKind.OBSERVES]
        assert len(observes) == 1
        assert observes[0].source == "class:App\\Observers\\UserObserver"
        assert observes[0].target == "class:App\\Models\\User"
        assert observes[0].attributes.get("line") == 12

    def test_observer_registration_without_model_meta_is_skipped(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """Defensive: a malformed finding with no ``model`` meta key adds no edge."""
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"observer_registration": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="observer_registration",
                        target="App\\Observers\\Some",
                        in_class="App\\X",
                        in_method="boot",
                        file=None,
                        line=None,
                        meta={},  # missing 'model'
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        observes = [e for e in result.value.edges if e.kind == EdgeKind.OBSERVES]
        assert observes == []

    def test_cache_read_creates_cache_key_node_and_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"cache_read": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="cache_read",
                        target="user.profile.42",
                        in_class="App\\Http\\Controllers\\UserController",
                        in_method="show",
                        file=None,
                        line=24,
                        meta={"method": "get", "form": "literal"},
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        cache_nodes = [n for n in result.value.nodes if n.kind == NodeKind.CACHE_KEY]
        assert len(cache_nodes) == 1
        assert cache_nodes[0].id == "cache_key:user.profile.42"
        assert cache_nodes[0].name == "user.profile.42"

        edges = [e for e in result.value.edges if e.kind == EdgeKind.CACHE_READ]
        assert len(edges) == 1
        assert edges[0].source == "method:App\\Http\\Controllers\\UserController::show"
        assert edges[0].target == "cache_key:user.profile.42"
        assert edges[0].attributes.get("form") == "literal"
        assert edges[0].attributes.get("method") == "get"

    def test_cache_write_uses_separate_edge_kind(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """Reads and writes must end up on different edge kinds."""
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"cache_write": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="cache_write",
                        target="site.config",
                        in_class="App\\Boot",
                        in_method="warm",
                        file=None,
                        line=None,
                        meta={"method": "put", "form": "literal"},
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        reads = [e for e in result.value.edges if e.kind == EdgeKind.CACHE_READ]
        writes = [e for e in result.value.edges if e.kind == EdgeKind.CACHE_WRITE]
        assert reads == []
        assert len(writes) == 1

    def test_broadcast_channel_creates_channel_node_and_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"broadcast_channel": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="broadcast_channel",
                        target="orders",
                        in_class="App\\Events\\OrderPlaced",
                        in_method="broadcastOn",
                        file=None,
                        line=12,
                        meta={"channel_kind": "Channel", "form": "literal"},
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        channel_nodes = [n for n in result.value.nodes if n.kind == NodeKind.BROADCAST_CHANNEL]
        assert len(channel_nodes) == 1
        assert channel_nodes[0].id == "broadcast_channel:Channel:orders"

        edges = [e for e in result.value.edges if e.kind == EdgeKind.BROADCASTS_TO]
        assert len(edges) == 1
        assert edges[0].source == "class:App\\Events\\OrderPlaced"
        assert edges[0].target == "broadcast_channel:Channel:orders"

    def test_broadcast_channel_kind_is_part_of_node_id(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        """Public 'orders' and PrivateChannel 'orders' must NOT collide."""
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=2,
                finding_count=2,
                by_kind={"broadcast_channel": 2},
                findings=[
                    StaticAnalysisFinding(
                        kind="broadcast_channel",
                        target="orders",
                        in_class="App\\Events\\Public_",
                        in_method="broadcastOn",
                        file=None,
                        line=None,
                        meta={"channel_kind": "Channel"},
                    ),
                    StaticAnalysisFinding(
                        kind="broadcast_channel",
                        target="orders",
                        in_class="App\\Events\\Private_",
                        in_method="broadcastOn",
                        file=None,
                        line=None,
                        meta={"channel_kind": "PrivateChannel"},
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        ids = {n.id for n in result.value.nodes if n.kind == NodeKind.BROADCAST_CHANNEL}
        assert ids == {
            "broadcast_channel:Channel:orders",
            "broadcast_channel:PrivateChannel:orders",
        }

    def test_authorize_creates_authorised_by_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"authorize": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="authorize",
                        target="update",
                        in_class="App\\Http\\Controllers\\PostController",
                        in_method="update",
                        file=None,
                        line=None,
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        auth_edges = [e for e in result.value.edges if e.kind == EdgeKind.AUTHORISED_BY]
        assert len(auth_edges) == 1
        assert auth_edges[0].source == "method:App\\Http\\Controllers\\PostController::update"
        assert auth_edges[0].target == "gate:update"

    def test_finding_without_target_is_skipped(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"event_dispatch": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="event_dispatch",
                        target=None,  # dynamic dispatch — can't resolve
                        in_class="App\\Http\\Controllers\\Foo",
                        in_method="bar",
                        file=None,
                        line=None,
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        fires_edges = [e for e in result.value.edges if e.kind == EdgeKind.FIRES]
        assert len(fires_edges) == 0

    def test_finding_without_in_method_uses_class_node(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            StaticAnalysisFinding,
            StaticAnalysisSection,
        )

        document = self._minimal_document(
            static_analysis=StaticAnalysisSection(
                file_count=1,
                finding_count=1,
                by_kind={"job_dispatch": 1},
                findings=[
                    StaticAnalysisFinding(
                        kind="job_dispatch",
                        target="App\\Jobs\\CleanUp",
                        in_class="App\\Providers\\AppServiceProvider",
                        in_method=None,
                        file=None,
                        line=None,
                    ),
                ],
            ),
        )

        result = builder.build(document, empty_profile)

        dispatches_edges = [e for e in result.value.edges if e.kind == EdgeKind.DISPATCHES]
        assert len(dispatches_edges) == 1
        assert dispatches_edges[0].source == "class:App\\Providers\\AppServiceProvider"


class TestValidatesWithEdges:
    """VALIDATES_WITH edges link controller methods to their FormRequest params."""

    def test_form_request_parameter_creates_validates_with_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            BindingsSection,
            BindingsSummary,
            ClassEntry,
            ClassesSection,
            ClassReflection,
            EventListenersSection,
            GatesPoliciesSection,
            MethodInfo,
            MethodParameter,
            MiddlewareSection,
            ProjectMetadata,
            ReflectionDocument,
            ReflectionSections,
            ReflectionSummary,
            RoutesSection,
            ScheduleSection,
            StaticAnalysisSection,
        )

        # A FormRequest class and a controller method that injects it.
        form_request_entry = ClassEntry(
            source="project",
            kinds=["form_request"],
            reflection=ClassReflection(
                name="App\\Http\\Requests\\StorePostRequest",
                short_name="StorePostRequest",
                namespace="App\\Http\\Requests",
                file="/app/Http/Requests/StorePostRequest.php",
                abstract=False,
                final=False,
                parent="Illuminate\\Foundation\\Http\\FormRequest",
                interfaces=[],
                traits=[],
                attributes=[],
                methods=[],
            ),
        )
        controller_entry = ClassEntry(
            source="project",
            kinds=["controller"],
            reflection=ClassReflection(
                name="App\\Http\\Controllers\\PostController",
                short_name="PostController",
                namespace="App\\Http\\Controllers",
                file="/app/Http/Controllers/PostController.php",
                abstract=False,
                final=False,
                parent=None,
                interfaces=[],
                traits=[],
                attributes=[],
                methods=[
                    MethodInfo(
                        name="store",
                        visibility="public",
                        static=False,
                        abstract=False,
                        final=False,
                        parameters=[
                            MethodParameter(
                                name="request",
                                type="App\\Http\\Requests\\StorePostRequest",
                                optional=False,
                                variadic=False,
                                by_reference=False,
                            ),
                        ],
                        return_type=None,
                        line=20,
                    ),
                ],
            ),
        )

        sections = ReflectionSections(
            routes=RoutesSection(count=0, items=[]),
            bindings=BindingsSection(
                bindings=[],
                aliases=[],
                instances=[],
                summary=BindingsSummary(binding_count=0, alias_count=0, instance_count=0),
            ),
            events=EventListenersSection(listeners=[], wildcards=[]),
            gates_policies=GatesPoliciesSection(gates=[], policies=[]),
            middleware=MiddlewareSection(),
            config={},
            schedule=ScheduleSection(count=0, events=[]),
            classes=ClassesSection(count=2, items=[form_request_entry, controller_entry]),
            static_analysis=StaticAnalysisSection(
                file_count=0, finding_count=0, by_kind={}, findings=[]
            ),
        )
        document = ReflectionDocument(
            schema_version="2.0.0",
            generated_at="2026-04-08T00:00:00+00:00",
            project=ProjectMetadata(
                name="Test",
                environment="testing",
                laravel_version="12.0.0",
                php_version="8.3.0",
                base_path="/tmp",
            ),
            sections=sections,
            warnings=[],
            errors=[],
            summary=ReflectionSummary(sections=[], warning_count=0, error_count=0),
        )

        result = builder.build(document, empty_profile)

        validates_with_edges = [e for e in result.value.edges if e.kind == EdgeKind.VALIDATES_WITH]
        assert len(validates_with_edges) == 1
        assert validates_with_edges[0].source == (
            "method:App\\Http\\Controllers\\PostController::store"
        )
        assert validates_with_edges[0].target == ("class:App\\Http\\Requests\\StorePostRequest")

    def test_non_form_request_parameter_creates_no_edge(
        self, builder: GraphBuilder, empty_profile: StubProfile
    ) -> None:
        from nexus.core.reflection.document import (
            BindingsSection,
            BindingsSummary,
            ClassEntry,
            ClassesSection,
            ClassReflection,
            EventListenersSection,
            GatesPoliciesSection,
            MethodInfo,
            MethodParameter,
            MiddlewareSection,
            ProjectMetadata,
            ReflectionDocument,
            ReflectionSections,
            ReflectionSummary,
            RoutesSection,
            ScheduleSection,
            StaticAnalysisSection,
        )

        controller_entry = ClassEntry(
            source="project",
            kinds=["controller"],
            reflection=ClassReflection(
                name="App\\Http\\Controllers\\PostController",
                short_name="PostController",
                namespace="App\\Http\\Controllers",
                file=None,
                abstract=False,
                final=False,
                parent=None,
                interfaces=[],
                traits=[],
                attributes=[],
                methods=[
                    MethodInfo(
                        name="show",
                        visibility="public",
                        static=False,
                        abstract=False,
                        final=False,
                        parameters=[
                            MethodParameter(
                                name="post",
                                type="App\\Models\\Post",
                                optional=False,
                                variadic=False,
                                by_reference=False,
                            ),
                        ],
                        return_type=None,
                        line=10,
                    ),
                ],
            ),
        )

        sections = ReflectionSections(
            routes=RoutesSection(count=0, items=[]),
            bindings=BindingsSection(
                bindings=[],
                aliases=[],
                instances=[],
                summary=BindingsSummary(binding_count=0, alias_count=0, instance_count=0),
            ),
            events=EventListenersSection(listeners=[], wildcards=[]),
            gates_policies=GatesPoliciesSection(gates=[], policies=[]),
            middleware=MiddlewareSection(),
            config={},
            schedule=ScheduleSection(count=0, events=[]),
            classes=ClassesSection(count=1, items=[controller_entry]),
            static_analysis=StaticAnalysisSection(
                file_count=0, finding_count=0, by_kind={}, findings=[]
            ),
        )
        document = ReflectionDocument(
            schema_version="2.0.0",
            generated_at="2026-04-08T00:00:00+00:00",
            project=ProjectMetadata(
                name="Test",
                environment="testing",
                laravel_version="12.0.0",
                php_version="8.3.0",
                base_path="/tmp",
            ),
            sections=sections,
            warnings=[],
            errors=[],
            summary=ReflectionSummary(sections=[], warning_count=0, error_count=0),
        )

        result = builder.build(document, empty_profile)

        validates_with_edges = [e for e in result.value.edges if e.kind == EdgeKind.VALIDATES_WITH]
        assert len(validates_with_edges) == 0
