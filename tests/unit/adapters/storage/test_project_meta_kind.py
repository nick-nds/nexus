"""ProjectMeta gains kind/package/build_mode/source_path in schema 1.1."""

from __future__ import annotations

from nexus.adapters.storage.project_storage import ProjectMeta
from nexus.core.reflection.document import PackageAuthor, PackageMetadata


def test_default_kind_is_project() -> None:
    meta = ProjectMeta(project_slug="foo", project_path="/tmp/foo")
    assert meta.kind == "project"
    assert meta.package is None
    assert meta.build_mode is None
    assert meta.source_path is None


def test_package_meta_has_required_fields() -> None:
    meta = ProjectMeta(
        project_slug="spatie--laravel-permission",
        project_path="/home/me/.nexus/projects/spatie--laravel-permission",
        kind="package",
        package=PackageMetadata(vendor="spatie", name="laravel-permission", version="v6.18.0"),
        build_mode="nexus-driven",
        source_path="/home/me/dev/laravel-permission",
    )

    assert meta.kind == "package"
    assert meta.package is not None
    assert meta.package.vendor == "spatie"
    assert meta.build_mode == "nexus-driven"
    assert meta.source_path == "/home/me/dev/laravel-permission"


def test_package_meta_carries_full_attribution() -> None:
    """ProjectMeta mirrors the full attribution surface (decision #10)."""
    meta = ProjectMeta(
        project_slug="spatie--laravel-permission",
        project_path="/home/me/.nexus/projects/spatie--laravel-permission",
        kind="package",
        package=PackageMetadata(
            vendor="spatie",
            name="laravel-permission",
            version="v6.18.0",
            description="Permission handling for Laravel 8.0 and up",
            authors=[
                PackageAuthor(
                    name="Freek Van der Herten",
                    email="freek@spatie.be",
                    homepage="https://spatie.be",
                )
            ],
            license="MIT",
            homepage="https://github.com/spatie/laravel-permission",
        ),
        build_mode="nexus-driven",
        source_path="/home/me/dev/laravel-permission",
    )

    assert meta.package is not None
    assert meta.package.description == "Permission handling for Laravel 8.0 and up"
    assert meta.package.license == "MIT"
    assert meta.package.homepage == "https://github.com/spatie/laravel-permission"
    assert len(meta.package.authors) == 1
    assert meta.package.authors[0].name == "Freek Van der Herten"


def test_schema_version_default_is_1_1() -> None:
    meta = ProjectMeta(project_slug="foo", project_path="/tmp/foo")
    assert meta.schema_version == "1.1"


def test_old_1_0_metas_load_with_defaults() -> None:
    """Existing 1.0 meta files load fine - defaults fill missing keys."""
    raw = {
        "schema_version": "1.0",
        "project_slug": "foo",
        "project_path": "/tmp/foo",
    }
    meta = ProjectMeta.model_validate(raw)
    assert meta.kind == "project"
    assert meta.package is None
