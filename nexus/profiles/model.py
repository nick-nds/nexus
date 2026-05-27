"""Pydantic models for the built-in profile YAML format.

The shape here mirrors the ``internal_docs/11-profile-system.md``
§"Built-in profile format" example. Each built-in profile lives as a
YAML file under :mod:`nexus.profiles.builtin` and is loaded once per
process.

Why a separate model from :class:`nexus.config.project_profile.ProjectProfile`:

* Built-in profiles carry **detection signals** - the rules the
  auto-detector uses to score the profile against a project tree.
  User ``nexus.yml`` files do not (the user picks a profile
  explicitly, or lets auto-detection work from the built-ins).
* Built-ins have ``name`` / ``display_name`` / ``description``; user
  profiles do not.
* Keeping the two models separate means evolving one doesn't risk
  breaking the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProfileModulesConvention(BaseModel):
    """DDD module layout, as described in a built-in profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str
    layers: list[str] = Field(default_factory=list)


class ProfileConventions(BaseModel):
    """The convention rules a built-in profile contributes.

    See the example in ``internal_docs/11-profile-system.md``
    §"Built-in profile format". The keys here mirror the YAML exactly.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    custom_bases: dict[str, str] = Field(default_factory=dict)
    custom_suffixes: dict[str, str] = Field(default_factory=dict)
    modules: ProfileModulesConvention | None = None


SignalKind = Literal[
    "path_exists",
    "composer_requires",
    "class_suffix_frequency",
    "interface_usage",
]


class DetectionSignal(BaseModel):
    """One rule the auto-detector uses to score a profile against a project.

    Each signal has a ``kind`` that selects an evaluator in
    :mod:`nexus.profiles.detector`, plus kind-specific parameters, plus
    a weight that contributes to the final score when the signal fires.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SignalKind
    weight: int = Field(ge=1, le=100)
    # Kind-specific parameters. We keep these as optional fields on one
    # model rather than per-kind subclasses so the YAML stays simple and
    # the evaluator dispatch is a single switch.
    path: str | None = None
    package: str | None = None
    suffix: str | None = None
    threshold: int | None = None
    interface: str | None = None


class Detection(BaseModel):
    """Container for a profile's list of detection signals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signals: list[DetectionSignal] = Field(default_factory=list)


class ProfileYaml(BaseModel):
    """The top-level shape of a built-in profile YAML file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    display_name: str
    description: str
    detection: Detection = Field(default_factory=Detection)
    conventions: ProfileConventions = Field(default_factory=ProfileConventions)


@dataclass(frozen=True, slots=True)
class LoadedProfile:
    """A profile that has been parsed from YAML and is ready to use.

    Conforms structurally to the :class:`nexus.core.protocols.Profile`
    protocol so the graph builder and other consumers can accept it
    without importing this class.
    """

    name: str
    display_name: str
    description: str
    custom_bases: dict[str, str]
    custom_suffixes: dict[str, str]
    modules: ProfileModulesConvention | None
    signals: tuple[DetectionSignal, ...]

    @classmethod
    def from_yaml(cls, yaml_model: ProfileYaml) -> LoadedProfile:
        """Convert a validated YAML model into a :class:`LoadedProfile`."""
        return cls(
            name=yaml_model.name,
            display_name=yaml_model.display_name,
            description=yaml_model.description,
            custom_bases=dict(yaml_model.conventions.custom_bases),
            custom_suffixes=dict(yaml_model.conventions.custom_suffixes),
            modules=yaml_model.conventions.modules,
            signals=tuple(yaml_model.detection.signals),
        )
