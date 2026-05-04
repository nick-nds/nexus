"""Reflection JSON loader and typed models.

This package owns the boundary between the PHP extractor and the Python
side. The Composer package writes a ``reflection.json`` document with a
declared :data:`SCHEMA_MAJOR` version; the loader here parses it through
Pydantic models and rejects any document whose major version we don't
understand.

Pydantic v2 with ``extra="forbid"`` is used so that schema drift is loud:
if Phase 1 adds a new field without bumping the schema version, our
tests fail immediately rather than silently dropping data.
"""

from nexus.core.reflection.document import (
    SCHEMA_MAJOR,
    BindingItem,
    BindingsSection,
    ClassEntry,
    ClassesSection,
    ConfigSection,
    EventListenersSection,
    GatesPoliciesSection,
    MiddlewareSection,
    ProjectMetadata,
    ReflectionDocument,
    RoutesSection,
    ScheduleEvent,
    ScheduleSection,
    StaticAnalysisFinding,
    StaticAnalysisSection,
)
from nexus.core.reflection.loader import (
    ReflectionLoadError,
    ReflectionVersionError,
    load_reflection,
)

__all__ = [
    "SCHEMA_MAJOR",
    "BindingItem",
    "BindingsSection",
    "ClassEntry",
    "ClassesSection",
    "ConfigSection",
    "EventListenersSection",
    "GatesPoliciesSection",
    "MiddlewareSection",
    "ProjectMetadata",
    "ReflectionDocument",
    "ReflectionLoadError",
    "ReflectionVersionError",
    "RoutesSection",
    "ScheduleEvent",
    "ScheduleSection",
    "StaticAnalysisFinding",
    "StaticAnalysisSection",
    "load_reflection",
]
