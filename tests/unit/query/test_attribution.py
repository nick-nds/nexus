"""build_attribution() turns a ProjectMeta into the response envelope's package block."""

from __future__ import annotations

from nexus.adapters.storage.project_storage import ProjectMeta
from nexus.core.query.attribution import build_attribution, render_attribution_footer
from nexus.core.reflection.document import PackageAuthor, PackageMetadata


def _full_meta() -> ProjectMeta:
    return ProjectMeta(
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


def test_returns_none_for_project_kind() -> None:
    meta = ProjectMeta(project_slug="my-app", project_path="/x")
    assert build_attribution(meta) is None


def test_returns_full_block_for_package_kind() -> None:
    block = build_attribution(_full_meta())
    assert block is not None
    assert block["vendor"] == "spatie"
    assert block["name"] == "laravel-permission"
    assert block["version"] == "v6.18.0"
    assert block["description"] == "Permission handling for Laravel 8.0 and up"
    assert block["license"] == "MIT"
    assert block["homepage"] == "https://github.com/spatie/laravel-permission"
    assert block["authors"] == [
        {
            "name": "Freek Van der Herten",
            "email": "freek@spatie.be",
            "homepage": "https://spatie.be",
            "role": None,
        }
    ]


def test_footer_renders_full_attribution() -> None:
    text = render_attribution_footer(_full_meta())
    assert "Indexed from spatie/laravel-permission@v6.18.0" in text
    assert "by Freek Van der Herten <freek@spatie.be>" in text
    assert "MIT" in text
    assert "https://github.com/spatie/laravel-permission" in text


def test_footer_omits_missing_license_and_homepage() -> None:
    meta = _full_meta()
    assert meta.package is not None
    minimal = meta.model_copy(
        update={
            "package": meta.package.model_copy(
                update={"license": None, "homepage": None},
            )
        }
    )
    text = render_attribution_footer(minimal)
    assert "MIT" not in text
    assert "https://" not in text
    assert "·  ·" not in text
    assert not text.rstrip().endswith("·")


def test_footer_truncates_many_authors() -> None:
    meta = _full_meta()
    assert meta.package is not None
    many_authors = [PackageAuthor(name=f"Author {i}") for i in range(7)]
    big = meta.model_copy(
        update={"package": meta.package.model_copy(update={"authors": many_authors})}
    )
    text = render_attribution_footer(big)
    assert "Author 0" in text
    assert "Author 1" in text
    assert "Author 2" in text
    assert "+4 more" in text
    assert "Author 6" not in text


def test_footer_with_no_authors() -> None:
    meta = _full_meta()
    assert meta.package is not None
    no_authors = meta.model_copy(
        update={"package": meta.package.model_copy(update={"authors": []})}
    )
    text = render_attribution_footer(no_authors)
    assert "Indexed from" in text
    assert " by " not in text
