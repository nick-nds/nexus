"""Built-in profile YAML loader.

Built-in profiles ship as YAML files under :mod:`nexus.profiles.builtin`.
:func:`load_builtin_profiles` reads all of them once via
:mod:`importlib.resources`, parses each through :class:`ProfileYaml`,
converts to :class:`LoadedProfile`, and returns a
:class:`BuiltInProfiles` mapping.

The loader caches nothing implicitly — callers that want a per-process
singleton should memoise at their own boundary. Keeping the loader
stateless means tests can rebuild the set cleanly between runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from nexus.profiles.model import LoadedProfile, ProfileYaml

if TYPE_CHECKING:
    from collections.abc import Iterator


class BuiltInProfileError(Exception):
    """Raised when a built-in profile YAML is malformed.

    This is a developer error — built-in profiles are shipped with the
    package — so a non-Nexus-typed exception would be acceptable, but
    using a typed one lets tests assert on it cleanly.
    """


@dataclass(frozen=True, slots=True)
class BuiltInProfiles:
    """Read-only mapping of built-in profile name → :class:`LoadedProfile`.

    Iteration order is deterministic (sorted by name) so the
    auto-detector's "ranked by score, ties broken by name" semantics
    are stable.
    """

    profiles: tuple[LoadedProfile, ...]

    def __iter__(self) -> Iterator[LoadedProfile]:
        """Iterate profiles in deterministic (sorted-by-name) order."""
        return iter(self.profiles)

    def __len__(self) -> int:
        """Return the number of loaded profiles."""
        return len(self.profiles)

    def __contains__(self, name: object) -> bool:
        """Return True if a profile with the given name is present."""
        return any(p.name == name for p in self.profiles)

    def get(self, name: str) -> LoadedProfile | None:
        """Look up a profile by name. Returns ``None`` if absent."""
        for profile in self.profiles:
            if profile.name == name:
                return profile
        return None

    def names(self) -> list[str]:
        """Return every profile name in deterministic order."""
        return [p.name for p in self.profiles]


def load_builtin_profiles() -> BuiltInProfiles:
    """Load every built-in profile YAML shipped with the package.

    Raises:
        BuiltInProfileError: one of the YAML files is malformed or
            fails Pydantic validation. This is a packaging bug worth
            surfacing loudly.
    """
    builtin_dir = resources.files("nexus.profiles.builtin")

    loaded: list[LoadedProfile] = []

    for entry in builtin_dir.iterdir():
        name = entry.name
        if not name.endswith(".yml") and not name.endswith(".yaml"):
            continue

        try:
            raw = yaml.safe_load(entry.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise BuiltInProfileError(
                f"Built-in profile {name} contains invalid YAML: {e}",
            ) from e

        if not isinstance(raw, dict):
            raise BuiltInProfileError(
                f"Built-in profile {name} must be a YAML mapping at the top level.",
            )

        try:
            model = ProfileYaml.model_validate(raw)
        except ValidationError as e:
            raise BuiltInProfileError(
                f"Built-in profile {name} failed validation: {e}",
            ) from e

        loaded.append(LoadedProfile.from_yaml(model))

    # Sort by name so iteration is deterministic across machines.
    loaded.sort(key=lambda p: p.name)

    return BuiltInProfiles(profiles=tuple(loaded))
