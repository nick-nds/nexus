"""Project-level ``nexus.yml`` profile.

Lives in the project root, committed to git. Describes the project's
structure, its conventions, and any user overrides on top of the
built-in profile the auto-detector selected. See
``internal_docs/11-profile-system.md`` §"Profile structure" for the
design and field-by-field rationale.

The design rule for this file: every field is optional. A minimal
``nexus.yml`` is three lines:

.. code-block:: yaml

    project:
        slug: my-crm

Everything else has sensible defaults and is filled in from the
auto-detected built-in profile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from nexus.config.loader import (
    check_schema_major,
    load_yaml_document,
    validate_model,
)

if TYPE_CHECKING:
    from pathlib import Path

PROJECT_PROFILE_SCHEMA_MAJOR = 1


class ProjectMetadata(BaseModel):
    """Required project-identifying fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(
        description=(
            "Stable identifier used to namespace the project's storage "
            "directory under ~/.nexus/projects/. Alphanumeric + dashes."
        ),
    )
    name: str | None = None
    description: str | None = None


class ModulesConvention(BaseModel):
    """DDD-style module layout declaration.

    Projects that use Domain-Driven Design organise code under
    ``app/Modules/<Module>/<Layer>/`` or similar. The extractor cannot
    infer module boundaries from runtime state; the profile declares
    them explicitly via this block so Phase 3's chunker and Phase 4's
    query engine can attribute nodes to modules.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str = Field(
        description=(
            "Glob-style path pattern with `{module}` as a placeholder. "
            "Example: `app/Modules/{module}/**`."
        ),
    )
    layers: list[str] = Field(
        default_factory=list,
        description="Known layer names, typically [Domain, Application, Infrastructure].",
    )


class ProjectProfileConventions(BaseModel):
    """Optional overrides and additions on top of the detected profile.

    These mirror the ``custom_bases`` / ``custom_suffixes`` concepts
    from the built-in profile format so a project can add one or two
    project-specific conventions without shipping a whole new built-in.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    custom_bases: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of fully-qualified base-class FQN → emitted kind label. "
            "Applied to any class whose parent matches."
        ),
    )
    custom_suffixes: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of class-name suffix → emitted kind label. Applied to "
            "any class whose name ends with the suffix."
        ),
    )
    modules: ModulesConvention | None = None


class IndexingSettings(BaseModel):
    """Per-project indexing knobs.

    Mostly opt-outs: which directories to exclude, whether to include
    tests, which vendor packages to pull in despite the default
    vendor-skip behaviour.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    include_vendor: bool = False
    include_tests: bool = False
    include_blade: bool = True
    exclude_paths: list[str] = Field(default_factory=list)
    include_vendor_packages: list[str] = Field(default_factory=list)


class ProjectProfile(BaseModel):
    """The contents of ``./nexus.yml``.

    Loaded once at the start of an indexing run. The loader fills in
    defaults for optional sections so downstream consumers don't have
    to handle ``None`` cases everywhere.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = f"{PROJECT_PROFILE_SCHEMA_MAJOR}.0"
    project: ProjectMetadata
    profile: str | None = Field(
        default=None,
        description=(
            "Explicit built-in profile name override. If omitted, the "
            "auto-detector picks the best match."
        ),
    )
    profile_file: str | None = Field(
        default=None,
        description=(
            "Path to a custom profile YAML inside the project. Mutually exclusive with `profile`."
        ),
    )
    conventions: ProjectProfileConventions = Field(default_factory=ProjectProfileConventions)
    indexing: IndexingSettings = Field(default_factory=IndexingSettings)
    # Embedder override is the one place where a project preference
    # legitimately supersedes user global config - teams standardising
    # on a specific embedder for reproducibility across contributors.
    embedder: dict[str, str] | None = None


def load_project_profile(path: Path) -> ProjectProfile:
    """Load ``./nexus.yml`` from a project directory.

    Raises:
        ConfigNotFoundError: file does not exist.
        ConfigParseError: file is not valid YAML or fails schema validation.
        ConfigVersionError: file declares an incompatible major version.
    """
    raw = load_yaml_document(path)
    check_schema_major(raw, expected_major=PROJECT_PROFILE_SCHEMA_MAJOR, source=path)
    return validate_model(ProjectProfile, raw, source=path)
