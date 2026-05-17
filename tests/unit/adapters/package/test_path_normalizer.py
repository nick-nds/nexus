"""Path normalizer rewrites file paths to <package_root>-relative."""

from __future__ import annotations

from pathlib import Path

from nexus.adapters.package.path_normalizer import normalize_paths
from nexus.core.reflection.document import (
    BindingItem,
    BindingsSection,
    BindingsSummary,
    ClassEntry,
    ClassesSection,
    ClassReflection,
    ConcreteBinding,
    EventListenerEntry,
    EventListenersSection,
    GateCallback,
    GateEntry,
    GatesPoliciesSection,
    ListenerCallback,
    PackageMetadata,
    ProjectMetadata,
    ReflectionDocument,
    ReflectionSections,
    ReflectionSummary,
    RouteAction,
    RouteItem,
    RoutesSection,
    StaticAnalysisFinding,
    StaticAnalysisSection,
)


def _minimal_class_reflection(file_path: str) -> ClassReflection:
    return ClassReflection(
        name="App\\Models\\X",
        short_name="X",
        namespace="App\\Models",
        file=file_path,
        abstract=False,
        final=False,
        methods=[],
    )


def _minimal_doc(
    class_file: str,
    sa_file: str,
    route_file: str | None = None,
    gate_file: str | None = None,
    binding_file: str | None = None,
    listener_file: str | None = None,
) -> ReflectionDocument:
    """Build a minimal package-mode document with configurable file paths."""
    sections: dict[str, object] = {
        "classes": ClassesSection(
            count=1,
            items=[
                ClassEntry(
                    source="project",
                    kinds=["model"],
                    reflection=_minimal_class_reflection(class_file),
                )
            ],
        ),
        "static_analysis": StaticAnalysisSection(
            file_count=1,
            finding_count=1,
            findings=[
                StaticAnalysisFinding(
                    kind="missing_return_type",
                    file=sa_file,
                )
            ],
        ),
    }
    if route_file is not None:
        sections["routes"] = RoutesSection(
            count=1,
            items=[
                RouteItem(
                    uri="/test",
                    methods=["GET"],
                    action=RouteAction(kind="closure", file=route_file),
                )
            ],
        )
    if gate_file is not None:
        sections["gates_policies"] = GatesPoliciesSection(
            gates=[
                GateEntry(
                    ability="update",
                    callback=GateCallback(kind="closure", file=gate_file),
                )
            ],
            policies=[],
        )
    if binding_file is not None:
        sections["bindings"] = BindingsSection(
            bindings=[
                BindingItem(
                    abstract="App\\Contracts\\Foo",
                    shared=True,
                    concrete=ConcreteBinding(kind="closure", file=binding_file),
                )
            ],
            aliases=[],
            instances=[],
            summary=BindingsSummary(
                binding_count=1,
                alias_count=0,
                instance_count=0,
            ),
        )
    if listener_file is not None:
        sections["events"] = EventListenersSection(
            listeners=[
                EventListenerEntry(
                    event="App\\Events\\OrderPlaced",
                    listeners=[ListenerCallback(kind="closure", file=listener_file)],
                )
            ]
        )

    populated_keys = list(sections.keys())

    return ReflectionDocument(
        schema_version="2.0.0",
        generated_at="2026-05-17T00:00:00Z",
        project=ProjectMetadata(
            name="test-app",
            environment="testing",
            laravel_version="11.0.0",
            php_version="8.2.0",
            base_path="/scratch/vendor/foo/bar",
        ),
        kind="package",
        package=PackageMetadata(
            vendor="foo",
            name="bar",
            version="1.0.0",
        ),
        sections=ReflectionSections(**sections),
        summary=ReflectionSummary(
            sections=populated_keys,
            warning_count=0,
            error_count=0,
        ),
    )


# ---------------------------------------------------------------------------
# Core tests
# ---------------------------------------------------------------------------


def test_paths_under_vendor_path_become_package_relative() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"
    class_file = f"{vendor_path}/src/Models/X.php"
    sa_file = f"{vendor_path}/src/Models/X.php"

    doc = _minimal_doc(class_file=class_file, sa_file=sa_file)
    out = normalize_paths(doc, package_root=Path(package_root), vendor_path=Path(vendor_path))

    assert out.sections.classes is not None
    assert out.sections.classes.items[0].reflection.file == "src/Models/X.php"

    assert out.sections.static_analysis is not None
    assert out.sections.static_analysis.findings[0].file == "src/Models/X.php"

    assert out.project.base_path == package_root


def test_paths_outside_vendor_path_pass_through_unchanged() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"
    unrelated = "/some/unrelated/path.php"

    doc = _minimal_doc(class_file=unrelated, sa_file=unrelated)
    out = normalize_paths(doc, package_root=Path(package_root), vendor_path=Path(vendor_path))

    assert out.sections.classes is not None
    assert out.sections.classes.items[0].reflection.file == unrelated


def test_normalizer_is_idempotent() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"
    class_file = f"{vendor_path}/src/X.php"

    doc = _minimal_doc(class_file=class_file, sa_file=class_file)
    once = normalize_paths(doc, package_root=Path(package_root), vendor_path=Path(vendor_path))
    twice = normalize_paths(once, package_root=Path(package_root), vendor_path=Path(vendor_path))

    assert once == twice


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_route_action_file_under_vendor_is_rewritten() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"
    route_file = f"{vendor_path}/routes/web.php"

    doc = _minimal_doc(
        class_file="/other.php",
        sa_file="/other.php",
        route_file=route_file,
    )
    out = normalize_paths(doc, package_root=Path(package_root), vendor_path=Path(vendor_path))

    assert out.sections.routes is not None
    assert out.sections.routes.items[0].action.file == "routes/web.php"


# ---------------------------------------------------------------------------
# Gates / policies
# ---------------------------------------------------------------------------


def test_gate_callback_file_under_vendor_is_rewritten() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"
    gate_file = f"{vendor_path}/src/Policies/GateDefiner.php"

    doc = _minimal_doc(
        class_file="/other.php",
        sa_file="/other.php",
        gate_file=gate_file,
    )
    out = normalize_paths(doc, package_root=Path(package_root), vendor_path=Path(vendor_path))

    assert out.sections.gates_policies is not None
    assert out.sections.gates_policies.gates[0].callback.file == "src/Policies/GateDefiner.php"


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


def test_binding_concrete_file_under_vendor_is_rewritten() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"
    binding_file = f"{vendor_path}/src/ServiceProvider.php"

    doc = _minimal_doc(
        class_file="/other.php",
        sa_file="/other.php",
        binding_file=binding_file,
    )
    out = normalize_paths(doc, package_root=Path(package_root), vendor_path=Path(vendor_path))

    assert out.sections.bindings is not None
    assert out.sections.bindings.bindings[0].concrete.file == "src/ServiceProvider.php"


# ---------------------------------------------------------------------------
# Events / listeners
# ---------------------------------------------------------------------------


def test_listener_file_under_vendor_is_rewritten() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"
    listener_file = f"{vendor_path}/src/Listeners/SendNotification.php"

    doc = _minimal_doc(
        class_file="/other.php",
        sa_file="/other.php",
        listener_file=listener_file,
    )
    out = normalize_paths(doc, package_root=Path(package_root), vendor_path=Path(vendor_path))

    assert out.sections.events is not None
    assert (
        out.sections.events.listeners[0].listeners[0].file == "src/Listeners/SendNotification.php"
    )


# ---------------------------------------------------------------------------
# None file field passes through cleanly
# ---------------------------------------------------------------------------


def test_none_file_field_is_preserved() -> None:
    vendor_path = "/scratch/vendor/foo/bar"
    package_root = "/home/me/dev/foo-bar"

    # We can't use _minimal_doc for this because the helper always supplies a file.
    # Build a doc directly with None file fields to exercise the None branch.
    doc_with_none = ReflectionDocument(
        schema_version="2.0.0",
        generated_at="2026-05-17T00:00:00Z",
        project=ProjectMetadata(
            name="test-app",
            environment="testing",
            laravel_version="11.0.0",
            php_version="8.2.0",
            base_path="/scratch/vendor/foo/bar",
        ),
        kind="package",
        package=PackageMetadata(vendor="foo", name="bar", version="1.0.0"),
        sections=ReflectionSections(
            classes=ClassesSection(
                count=1,
                items=[
                    ClassEntry(
                        source="project",
                        kinds=["model"],
                        reflection=ClassReflection(
                            name="App\\X",
                            short_name="X",
                            namespace="App",
                            file=None,
                            abstract=False,
                            final=False,
                            methods=[],
                        ),
                    )
                ],
            ),
            static_analysis=StaticAnalysisSection(
                file_count=0,
                finding_count=1,
                findings=[StaticAnalysisFinding(kind="missing_return_type", file=None)],
            ),
        ),
        summary=ReflectionSummary(
            sections=["classes", "static_analysis"],
            warning_count=0,
            error_count=0,
        ),
    )

    out = normalize_paths(
        doc_with_none, package_root=Path(package_root), vendor_path=Path(vendor_path)
    )

    assert out.sections.classes is not None
    assert out.sections.classes.items[0].reflection.file is None

    assert out.sections.static_analysis is not None
    assert out.sections.static_analysis.findings[0].file is None
