"""Phase 4 end-to-end validation against real indexed projects.

Opens the persisted helm-v7 and CRM indexes produced during the
Phase 3 validation runs and exercises a representative slice of
the structural tools against them. Output is human-readable so
it can be pasted into STATUS.md as evidence.

Run with::

    .venv/bin/python scripts/phase4_validate.py

Does **not** exercise ``semantic_search`` unless ``NEXUS_RUN_OLLAMA``
is set — that tool needs a live Ollama daemon so the query
vector can be produced with the same embedder the chunks were
written with.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from nexus.adapters.storage import ProjectStorage
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.classifier import QueryClassifier
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools

# Known-good persisted indexes from the Phase 3 validation runs.
INDEXES = [
    (
        "helm-v7",
        Path("/tmp/nexus-phase3-0uehqrzp/.nexus"),
        "momskitchen-smoketest",
    ),
    (
        "crm",
        Path("/tmp/nexus-phase3-ukfoaf6d/.nexus"),
        "momskitchen-smoketest",
    ),
]


def build_engine(root: Path, slug: str) -> QueryEngine:
    storage = ProjectStorage(root=root, slug=slug)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    return QueryEngine(registry, ctx)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def run_structural_suite(name: str, engine: QueryEngine) -> None:
    print(f"\n########## {name} ##########")

    section("list_routes (summary)")
    routes = engine.query("list_routes")
    print(f"total routes: {routes.total}")
    sample = routes.routes[:5]
    for r in sample:
        print(f"  {','.join(r.methods):>8}  {r.uri}")
    if routes.truncated:
        print(f"  (truncated: {routes.truncated_lists})")

    section("list_routes filter: method=POST")
    posts = engine.query("list_routes", {"method": "POST"})
    print(f"POST routes: {posts.total}")

    # Pick the first route that has an actual controller handler —
    # closures would produce a blank trace and miss the interesting
    # describe_class follow-up.
    controller_route = next(
        (r for r in routes.routes if r.action_kind == "controller" and r.controller),
        routes.routes[0] if routes.routes else None,
    )

    if controller_route is not None:
        first = controller_route
        section(f"trace_route {first.methods[0]} {first.uri}")
        trace = engine.query(
            "trace_route",
            {"method": first.methods[0], "uri": first.uri},
        )
        if trace.error is not None:
            print(f"  error: {trace.error}")
        else:
            print(f"  handler: {trace.handler}")
            print(f"  middleware ({len(trace.middleware)}):")
            for mw in trace.middleware[:5]:
                print(f"    {mw}")
            print(f"  fires_events: {trace.fires_events[:5]}")
            print(f"  dispatches_jobs: {trace.dispatches_jobs[:5]}")
            print(f"  policies: {trace.policies[:3]}")

        controller = first.controller
        if controller:
            section(f"describe_class {controller}")
            cls = engine.query("describe_class", {"fqn": controller})
            if cls.error is not None:
                print(f"  error: {cls.error}")
            else:
                print(f"  kind: {cls.kind}")
                print(f"  parent: {cls.parent}")
                print(f"  methods ({len(cls.methods)}):")
                for m in cls.methods[:5]:
                    print(f"    {m.visibility or '?':>9} {m.name}")
                print(f"  related_routes: {len(cls.related_routes)}")

    # Events + listeners — pick the first event node that has at least
    # one listener wired up, regardless of project.
    section("find_listeners (first event)")
    classifier = QueryClassifier()  # not strictly needed; demo the API
    _ = classifier
    graph = engine.context.storage.graph().load()
    first_event = next(
        (n for n in graph.nodes if n.kind.value == "event"),
        None,
    )
    if first_event is not None:
        fqn = first_event.attributes.get("fqn") or first_event.name
        listeners = engine.query("find_listeners", {"event": fqn})
        print(f"  event: {fqn}")
        print(f"  listeners: {listeners.total}")
        for r in listeners.listeners[:3]:
            print(f"    {r.listener_fqn}")

    # find_implementations against an abstract parent class.
    section("find_implementations (App\\Http\\Controllers\\Controller)")
    impls = engine.query(
        "find_implementations",
        {
            "interface_fqn": "App\\Http\\Controllers\\Controller",
            "include_subclasses": True,
        },
    )
    if impls.error is not None:
        print(f"  error: {impls.error}")
    else:
        print(f"  subclasses: {impls.total}")
        for row in impls.implementations[:3]:
            print(f"    {row.via:>10}  {row.fqn}")

    section("registry contents")
    for entry in engine.registry.tools():
        print(
            f"  {entry.name:<25}  "
            f"budget={entry.tool_class.latency_budget_ms:>4}ms  "
            f"({entry.tier})",
        )


def run_classifier_demo() -> None:
    print("\n########## classifier demo ##########")
    classifier = QueryClassifier()
    prompts = [
        "POST /api/orders",
        "What does App\\Http\\Controllers\\UserController do?",
        "Tell me about App\\Models\\Order",
        "who listens to OrderCreated?",
        "where is SendWelcomeJob dispatched?",
        "who implements PaymentGateway?",
        "policy for App\\Models\\Invoice",
        "show all routes",
        "how do we send welcome emails?",
    ]
    for p in prompts:
        plan = classifier.classify(p)
        print(f"  {p!r}")
        print(f"    → {plan.tool}  args={plan.args}  conf={plan.confidence:.2f}")


def main() -> int:
    run_classifier_demo()

    any_present = False
    for name, root, slug in INDEXES:
        if not (root / "projects" / slug / "meta.json").exists():
            print(f"\n[skip] {name}: no index found at {root}")
            continue
        any_present = True
        try:
            engine = build_engine(root, slug)
            run_structural_suite(name, engine)
        except Exception as e:  # noqa: BLE001 - smoke test, we want the error visible
            print(f"[error] {name}: {type(e).__name__}: {e}")

    if not any_present:
        print("\nNo persisted indexes found; Phase 4 structural validation skipped.")
        return 1

    if os.environ.get("NEXUS_RUN_OLLAMA"):
        print("\n(Ollama semantic_search validation: not yet wired in this script.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
