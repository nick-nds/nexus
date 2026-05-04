"""Tests for nexus.core.reflection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nexus.core.reflection import (
    ReflectionDocument,
    ReflectionLoadError,
    ReflectionVersionError,
    load_reflection,
)
from nexus.core.reflection.loader import (
    ReflectionNotFoundError,
    ReflectionParseError,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "reflection-samples"
FIXTURE = FIXTURE_DIR / "momskitchen.json"


class TestRealFixture:
    """Loads the real momskitchen reflection.json captured during Phase 1.

    This is the most important test in this suite: every model must
    accept the actual JSON Phase 1 emits without modification. If the
    PHP side ships a schema change, this test fails first.
    """

    def test_loads_full_document(self) -> None:
        doc = load_reflection(FIXTURE)

        assert isinstance(doc, ReflectionDocument)
        assert doc.schema_version.startswith("2.")
        assert doc.project.name == "Momskitchen"
        assert doc.project.laravel_version.startswith("12.")

    def test_routes_section_populated(self) -> None:
        doc = load_reflection(FIXTURE)
        assert doc.sections.routes is not None
        assert doc.sections.routes.count > 0
        assert len(doc.sections.routes.items) == doc.sections.routes.count

    def test_route_action_kinds_are_known(self) -> None:
        doc = load_reflection(FIXTURE)
        assert doc.sections.routes is not None
        for route in doc.sections.routes.items:
            assert route.action.kind in ("controller", "closure", "unknown")
            if route.action.kind == "controller":
                assert route.action.controller is not None
                assert route.action.method is not None

    def test_bindings_section_populated(self) -> None:
        doc = load_reflection(FIXTURE)
        assert doc.sections.bindings is not None
        assert doc.sections.bindings.summary.binding_count > 0

    def test_classes_section_populated(self) -> None:
        doc = load_reflection(FIXTURE)
        assert doc.sections.classes is not None
        assert doc.sections.classes.count > 0

        # At least one controller class should be present (Laravel 12
        # convention: classes in `\Controllers\` namespace).
        controllers = [c for c in doc.sections.classes.items if "controller" in c.kinds]
        assert len(controllers) > 0

    def test_static_analysis_section_populated(self) -> None:
        doc = load_reflection(FIXTURE)
        assert doc.sections.static_analysis is not None
        assert doc.sections.static_analysis.file_count > 0

    def test_middleware_aliases_loaded(self) -> None:
        doc = load_reflection(FIXTURE)
        assert doc.sections.middleware is not None
        assert "auth" in doc.sections.middleware.aliases

    def test_no_schema_drift(self) -> None:
        # Re-validating a freshly-loaded document must succeed; this
        # catches accidental field-name typos in the model.
        doc = load_reflection(FIXTURE)
        roundtripped = ReflectionDocument.model_validate(doc.model_dump(by_alias=True))
        assert roundtripped.project.name == doc.project.name


class TestErrorPaths:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ReflectionNotFoundError):
            load_reflection(tmp_path / "nope.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{ this is not json")

        with pytest.raises(ReflectionParseError):
            load_reflection(path)

    def test_missing_schema_version(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"project": {}, "sections": {}}))

        with pytest.raises(ReflectionParseError, match="schema_version"):
            load_reflection(path)

    def test_malformed_schema_version(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"schema_version": "two.zero.zero"}))

        with pytest.raises(ReflectionParseError, match="schema_version"):
            load_reflection(path)

    def test_unsupported_major_version(self, tmp_path: Path) -> None:
        path = tmp_path / "future.json"
        path.write_text(json.dumps({"schema_version": "3.0.0"}))

        with pytest.raises(ReflectionVersionError, match=r"3\.0\.0"):
            load_reflection(path)

    def test_validation_error_is_wrapped(self, tmp_path: Path) -> None:
        # Schema version is fine but the body is missing required fields.
        path = tmp_path / "incomplete.json"
        path.write_text(json.dumps({"schema_version": "2.0.0"}))

        with pytest.raises(ReflectionParseError):
            load_reflection(path)

    def test_root_must_be_object(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]")

        with pytest.raises(ReflectionParseError, match="must be a JSON object"):
            load_reflection(path)


class TestExtraFixtures:
    """Stress-test the loader against any larger reflection samples that
    happen to be present locally (crm, helm-v7). Always passes if only
    the committed momskitchen fixture is available; never fails CI for
    missing optional fixtures.
    """

    @pytest.mark.parametrize(
        "fixture_path",
        sorted(FIXTURE_DIR.glob("*.json")),
        ids=lambda p: p.stem,
    )
    def test_loads_without_error(self, fixture_path: Path) -> None:
        doc = load_reflection(fixture_path)
        assert doc.schema_version.startswith("2.")
        # Every project should at least have the routes section.
        assert doc.sections.routes is not None


class TestReflectionLoadErrorHierarchy:
    """All loader exceptions inherit from ReflectionLoadError so callers
    can catch the base class for "couldn't read the document for any reason"."""

    def test_not_found_inherits(self) -> None:
        assert issubclass(ReflectionNotFoundError, ReflectionLoadError)

    def test_parse_inherits(self) -> None:
        assert issubclass(ReflectionParseError, ReflectionLoadError)

    def test_version_inherits(self) -> None:
        assert issubclass(ReflectionVersionError, ReflectionLoadError)
