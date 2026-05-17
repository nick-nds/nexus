"""Validation rules for the kind/package fields added in schema 2.1.0."""

from __future__ import annotations

import pytest
from nexus.core.reflection.document import (
    PackageMetadata,
    ReflectionDocument,
)
from pydantic import ValidationError


def _base_payload() -> dict:
    """Minimal valid 2.0.0/2.1.0 document body."""
    return {
        "schema_version": "2.1.0",
        "generated_at": "2026-05-08T10:00:00Z",
        "project": {
            "name": "Test",
            "environment": "testing",
            "laravel_version": "11.0.0",
            "php_version": "8.3.0",
            "base_path": "/tmp",
        },
        "sections": {},
        "summary": {"sections": [], "warning_count": 0, "error_count": 0},
    }


def test_kind_defaults_to_project_when_omitted() -> None:
    doc = ReflectionDocument.model_validate(_base_payload())

    assert doc.kind == "project"
    assert doc.package is None


def test_kind_package_requires_package_field() -> None:
    payload = _base_payload()
    payload["kind"] = "package"

    with pytest.raises(ValidationError, match="package"):
        ReflectionDocument.model_validate(payload)


def test_kind_project_rejects_package_field() -> None:
    payload = _base_payload()
    payload["package"] = {"vendor": "spatie", "name": "permission", "version": "v6.18.0"}

    with pytest.raises(ValidationError, match="package"):
        ReflectionDocument.model_validate(payload)


def test_valid_package_document_loads_minimal_attribution() -> None:
    payload = _base_payload()
    payload["kind"] = "package"
    payload["package"] = {"vendor": "spatie", "name": "permission", "version": "v6.18.0"}

    doc = ReflectionDocument.model_validate(payload)

    assert doc.kind == "package"
    assert doc.package == PackageMetadata(vendor="spatie", name="permission", version="v6.18.0")
    assert doc.package.description is None
    assert doc.package.authors == []
    assert doc.package.license is None
    assert doc.package.homepage is None


def test_valid_package_document_loads_full_attribution() -> None:
    payload = _base_payload()
    payload["kind"] = "package"
    payload["package"] = {
        "vendor": "spatie",
        "name": "laravel-permission",
        "version": "v6.18.0",
        "description": "Permission handling for Laravel 8.0 and up",
        "authors": [
            {
                "name": "Freek Van der Herten",
                "email": "freek@spatie.be",
                "homepage": "https://spatie.be",
                "role": None,
            }
        ],
        "license": "MIT",
        "homepage": "https://github.com/spatie/laravel-permission",
    }

    doc = ReflectionDocument.model_validate(payload)

    assert doc.kind == "package"
    assert doc.package is not None
    assert doc.package.description == "Permission handling for Laravel 8.0 and up"
    assert doc.package.license == "MIT"
    assert doc.package.homepage == "https://github.com/spatie/laravel-permission"
    assert len(doc.package.authors) == 1
    assert doc.package.authors[0].name == "Freek Van der Herten"
    assert doc.package.authors[0].email == "freek@spatie.be"


def test_author_with_only_name_is_valid() -> None:
    payload = _base_payload()
    payload["kind"] = "package"
    payload["package"] = {
        "vendor": "foo",
        "name": "bar",
        "version": "1.0",
        "authors": [{"name": "Anonymous"}],
    }

    doc = ReflectionDocument.model_validate(payload)
    assert doc.package is not None
    assert doc.package.authors[0].name == "Anonymous"
    assert doc.package.authors[0].email is None
    assert doc.package.authors[0].homepage is None
    assert doc.package.authors[0].role is None


def test_2_0_0_documents_still_load() -> None:
    """Schema is back-compatible: a document without kind/package loads as kind=project."""
    payload = _base_payload()
    payload["schema_version"] = "2.0.0"
    payload.pop("kind", None)
    payload.pop("package", None)

    doc = ReflectionDocument.model_validate(payload)

    assert doc.kind == "project"
    assert doc.package is None
