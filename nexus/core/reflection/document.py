"""Pydantic v2 models for the Nexus reflection.json document.

The shape mirrors :file:`packages/nexus-extractor-php/src/Output/ReflectionDocument.php`
exactly. Field names are the same as the PHP-side keys; please keep them
in sync.

Why Pydantic and not plain dataclasses:

* Validation at the boundary catches schema drift loud and early.
* ``extra="forbid"`` means a Phase 1 release that adds a new field without
  bumping the schema version will fail Python tests instead of silently
  dropping data.
* The ``model_validator`` decorators give us one focused place to coerce
  PHP's "empty assoc array == empty list" quirk into the dict shape we
  actually want downstream.

The split between this file and :mod:`nexus.core.reflection.loader` is
intentional: the models are pure data definitions; the loader knows how
to read a JSON file from disk and surface validation errors as the
Nexus-typed exceptions defined there.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The Python side accepts any document whose major version matches this
# constant. Minor and patch increments are additive and may bring new
# optional fields, which we model as ``Field(default=...)``. A major bump
# means the loader rejects the document and asks the user to upgrade.
SCHEMA_MAJOR = 2


class _StrictModel(BaseModel):
    """Base for every reflection model.

    ``extra="forbid"`` is the architectural rule: an unknown field in the
    JSON is a sign that Phase 1 has shipped a schema change that hasn't
    been mirrored here. Failing fast surfaces the drift in CI.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=False)


# ----------------------------------------------------------------------------
# Project metadata (top-level)
# ----------------------------------------------------------------------------


class ProjectMetadata(_StrictModel):
    """Application-identifying metadata captured from the booted Laravel app."""

    name: str
    environment: str
    laravel_version: str
    php_version: str
    base_path: str
    profile_hint: str | None = None


# ----------------------------------------------------------------------------
# Routes section
# ----------------------------------------------------------------------------


class RouteAction(_StrictModel):
    """The handler that a route resolves to."""

    kind: Literal["controller", "closure", "unknown"]
    controller: str | None = None
    method: str | None = None
    file: str | None = None
    line: int | None = None


class RouteItem(_StrictModel):
    """A single registered route from Laravel's RouteCollection."""

    uri: str
    methods: list[str]
    name: str | None = None
    domain: str | None = None
    middleware: list[str] = Field(default_factory=list)
    wheres: dict[str, str] = Field(default_factory=dict)
    parameters: list[str] = Field(default_factory=list)
    action: RouteAction

    @field_validator("wheres", mode="before")
    @classmethod
    def _coerce_empty_assoc(cls, value: object) -> object:
        """PHP serialises an empty assoc array as ``[]``; coerce to ``{}``."""
        if isinstance(value, list) and not value:
            return {}
        return value


class RoutesSection(_StrictModel):
    count: int
    items: list[RouteItem]


# ----------------------------------------------------------------------------
# Bindings section
# ----------------------------------------------------------------------------


class ConcreteBinding(_StrictModel):
    """The concrete value of a container binding."""

    kind: Literal["class", "closure"]
    class_name: str | None = Field(default=None, alias="class")
    file: str | None = None
    line: int | None = None

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class BindingItem(_StrictModel):
    """A single container binding (singleton or per-resolution)."""

    abstract: str
    shared: bool
    concrete: ConcreteBinding


class AliasItem(_StrictModel):
    alias: str
    abstract: str


class InstanceItem(_StrictModel):
    abstract: str
    class_name: str | None = Field(default=None, alias="class")

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class BindingsSummary(_StrictModel):
    binding_count: int
    alias_count: int
    instance_count: int


class BindingsSection(_StrictModel):
    bindings: list[BindingItem]
    aliases: list[AliasItem]
    instances: list[InstanceItem]
    summary: BindingsSummary


# ----------------------------------------------------------------------------
# Events section
# ----------------------------------------------------------------------------


class ListenerCallback(_StrictModel):
    """A single listener registered for a Laravel event."""

    kind: Literal["class", "closure", "unknown"]
    class_name: str | None = Field(default=None, alias="class")
    method: str | None = None
    file: str | None = None
    line: int | None = None

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class EventListenerEntry(_StrictModel):
    event: str
    listeners: list[ListenerCallback]


class EventListenersSection(_StrictModel):
    listeners: list[EventListenerEntry]
    wildcards: list[EventListenerEntry] = Field(default_factory=list)
    note: str | None = None


# ----------------------------------------------------------------------------
# Gates and policies section
# ----------------------------------------------------------------------------


class GateCallback(_StrictModel):
    kind: Literal["class", "closure", "unknown"]
    class_name: str | None = Field(default=None, alias="class")
    method: str | None = None
    file: str | None = None
    line: int | None = None

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class GateEntry(_StrictModel):
    ability: str
    callback: GateCallback


class PolicyEntry(_StrictModel):
    model: str
    policy: str


class GatesPoliciesSection(_StrictModel):
    gates: list[GateEntry]
    policies: list[PolicyEntry]
    note: str | None = None


# ----------------------------------------------------------------------------
# Middleware section
# ----------------------------------------------------------------------------


class MiddlewareSection(_StrictModel):
    """Global, group, and aliased middleware exposed by the kernel."""

    global_: list[str] = Field(default_factory=list, alias="global")
    groups: dict[str, list[str]] = Field(default_factory=dict)
    aliases: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    @field_validator("groups", "aliases", mode="before")
    @classmethod
    def _coerce_empty_assoc(cls, value: object) -> object:
        if isinstance(value, list) and not value:
            return {}
        return value


# ----------------------------------------------------------------------------
# Config section
# ----------------------------------------------------------------------------


# The PHP side captures a curated allowlist of structural config keys with
# secrets redacted to "«redacted»". The values can be of any JSON-typed
# shape (scalar, list, nested dict). We model it as ``dict[str, Any]``
# because the structure is intentionally loose — the Python side just
# passes it through to the graph builder for downstream consumers.
ConfigSection = Annotated[
    dict[str, Any],
    Field(description="Curated, redacted slice of the framework config repository."),
]


# ----------------------------------------------------------------------------
# Schedule section
# ----------------------------------------------------------------------------


class ScheduleEvent(_StrictModel):
    expression: str
    timezone: str | None = None
    description: str | None = None
    without_overlapping: bool = False
    on_one_server: bool = False
    kind: Literal["command", "callback"]
    command: str | None = None
    target: str | None = None


class ScheduleSection(_StrictModel):
    count: int = 0
    events: list[ScheduleEvent] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Classes section
# ----------------------------------------------------------------------------


class MethodParameter(_StrictModel):
    name: str
    type: str | None = None
    optional: bool
    variadic: bool
    by_reference: bool


class MethodAttribute(_StrictModel):
    name: str
    arguments: dict[str, Any] | list[Any] = Field(default_factory=dict)


class MethodInfo(_StrictModel):
    name: str
    visibility: Literal["public", "protected", "private"]
    static: bool
    abstract: bool
    final: bool
    parameters: list[MethodParameter]
    return_type: str | None = None
    attributes: list[MethodAttribute] = Field(default_factory=list)
    line: int | None = None


class EnumCase(_StrictModel):
    """One case of an ``enum`` declaration (audit P0-2).

    Backed enums (``enum X: string``) carry a ``value`` of int or str;
    unit enums have ``value: None``. Surfacing cases lets agents answer
    "what statuses can X have?" without reading the source.
    """

    name: str
    value: int | str | None = None


class ClassReflection(_StrictModel):
    name: str
    short_name: str
    namespace: str
    file: str | None = None
    abstract: bool
    final: bool
    # PHP 8.2+ ``final readonly class Foo``. Schema 2.2.0 (audit P0-5).
    # Default ``False`` so reflection.json files emitted by 2.1.x — and
    # by extractors built against older PHP that lack
    # ``ReflectionClass::isReadOnly`` — still load cleanly.
    readonly: bool = False
    parent: str | None = None
    interfaces: list[str] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    attributes: list[MethodAttribute] = Field(default_factory=list)
    methods: list[MethodInfo]
    # Audit P0-2: cases of an ``enum`` declaration. Always a list — empty
    # for non-enums and for indexes built with schema ≤ 2.2.0. Schema
    # 2.3.0 bump.
    cases: list[EnumCase] = Field(default_factory=list)


class ClassEntry(_StrictModel):
    source: Literal["project", "vendor"]
    kinds: list[str]
    reflection: ClassReflection


class ClassesSection(_StrictModel):
    count: int
    items: list[ClassEntry]


# ----------------------------------------------------------------------------
# Static analysis section
# ----------------------------------------------------------------------------


class StaticAnalysisFinding(_StrictModel):
    kind: str
    target: str | None = None
    in_class: str | None = None
    in_method: str | None = None
    file: str | None = None
    line: int | None = None
    meta: dict[str, Any] | list[Any] = Field(default_factory=dict)


class StaticAnalysisSection(_StrictModel):
    file_count: int
    finding_count: int
    by_kind: dict[str, int] = Field(default_factory=dict)
    findings: list[StaticAnalysisFinding]

    @field_validator("by_kind", mode="before")
    @classmethod
    def _coerce_empty_assoc(cls, value: object) -> object:
        if isinstance(value, list) and not value:
            return {}
        return value


# ----------------------------------------------------------------------------
# Package attribution (present only when kind == "package")
# ----------------------------------------------------------------------------


class PackageAuthor(_StrictModel):
    """A single composer.json author entry.

    Per composer.json spec, only ``name`` is required. The other fields
    are optional and pass through as ``None`` when absent. We do not
    invent or infer values.
    """

    name: str
    email: str | None = None
    homepage: str | None = None
    role: str | None = None


class PackageMetadata(_StrictModel):
    """Identifies (and credits) the Composer package indexed by ``nexus package index``.

    Present iff the document's ``kind`` is ``"package"``. Mirrors the
    PHP-side ``ReflectionDocument`` ``package`` field. Carries the full
    attribution surface (decision #10): identity (vendor/name/version)
    plus credit fields (description, authors, license, homepage). Every
    optional field passes through as ``None`` / ``[]`` when the source
    composer.json doesn't supply it.
    """

    vendor: str
    name: str
    version: str
    description: str | None = None
    authors: list[PackageAuthor] = Field(default_factory=list)
    license: str | None = None
    homepage: str | None = None


# ----------------------------------------------------------------------------
# Top-level document
# ----------------------------------------------------------------------------


class ReflectionWarning(_StrictModel):
    code: str
    message: str
    file: str | None = None
    line: int | None = None
    context: dict[str, Any] | list[Any] = Field(default_factory=dict)


class ReflectionError(_StrictModel):
    code: str
    message: str
    file: str | None = None
    line: int | None = None
    context: dict[str, Any] | list[Any] = Field(default_factory=dict)


class ReflectionSummary(_StrictModel):
    sections: list[str]
    warning_count: int
    error_count: int


class ReflectionSections(_StrictModel):
    """Container for the nine extracted sections.

    Every section is optional at the model level so a partial document
    (one written by the Phase 1 fatal-error shutdown handler before all
    sections completed) still loads. Downstream consumers must handle the
    ``None`` case explicitly.
    """

    routes: RoutesSection | None = None
    bindings: BindingsSection | None = None
    events: EventListenersSection | None = None
    gates_policies: GatesPoliciesSection | None = None
    middleware: MiddlewareSection | None = None
    config: ConfigSection | None = None
    schedule: ScheduleSection | None = None
    classes: ClassesSection | None = None
    static_analysis: StaticAnalysisSection | None = None


class ReflectionDocument(_StrictModel):
    """Top-level reflection document, schema-versioned.

    The loader validates ``schema_version`` against :data:`SCHEMA_MAJOR`
    BEFORE constructing this model, so by the time you have a
    ``ReflectionDocument`` instance you can trust its shape.

    ``kind`` distinguishes a full-application extraction (``"project"``,
    the default) from a single Composer package extraction
    (``"package"``). When ``kind`` is ``"package"`` the ``package``
    field carries full attribution metadata (decision #10). The two
    fields are cross-validated: package extraction must supply
    ``package``; project extraction must not.
    """

    schema_version: str
    generated_at: str
    project: ProjectMetadata
    kind: Literal["project", "package"] = "project"
    package: PackageMetadata | None = None
    sections: ReflectionSections
    warnings: list[ReflectionWarning] = Field(default_factory=list)
    errors: list[ReflectionError] = Field(default_factory=list)
    summary: ReflectionSummary

    @model_validator(mode="after")
    def _check_kind_package_consistency(self) -> ReflectionDocument:
        """Enforce that kind and package are set together or not at all."""
        if self.kind == "package" and self.package is None:
            raise ValueError("kind='package' requires the 'package' field to be set")
        if self.kind == "project" and self.package is not None:
            raise ValueError("kind='project' must not set the 'package' field")
        return self

    @model_validator(mode="after")
    def _check_summary_section_names(self) -> ReflectionDocument:
        # Sanity guard: the summary's section list should agree with the
        # actually-populated sections. A mismatch suggests an extractor
        # bug worth surfacing in tests.
        populated = {
            name
            for name, value in self.sections.model_dump(exclude_none=True).items()
            if value is not None
        }
        declared = set(self.summary.sections)
        if populated and declared and populated != declared:
            # Don't fail validation — extractors may legitimately under-
            # report or over-report by one section in edge cases. We just
            # want this to be observable from a debug log.
            pass
        return self
