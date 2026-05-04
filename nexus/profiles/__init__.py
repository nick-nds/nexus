"""Profile system: built-in profiles, loader, and auto-detector.

A Nexus profile describes project-level conventions that runtime
extraction cannot see on its own: custom base classes, naming suffixes,
module layout, directory exclusions. v2 profiles are intentionally much
smaller than v1's Python-module profiles — runtime registries +
``instanceof`` classification already cover most of what v1 had to
detect manually.

Three concerns live in this package:

1. **Profile model** (:mod:`nexus.profiles.model`) — a Pydantic shape
   the YAML files parse into. Mirrors the built-in profile format
   described in ``internal_docs/11-profile-system.md``.

2. **Built-in profile loader** (:mod:`nexus.profiles.loader`) — reads
   the seven YAML files shipped under ``nexus/profiles/builtin/`` via
   :mod:`importlib.resources` and caches them for the lifetime of the
   process.

3. **Auto-detector** (:mod:`nexus.profiles.detector`) — walks a project
   directory, evaluates each built-in profile's detection signals,
   scores them, and returns a ranked list of matches. The detector
   does not read YAML, does not care about Laravel at runtime, and
   performs only filesystem reads — it runs before the PHP extractor
   so the user can be shown "we think this is an X project" before
   committing to a full indexing run.
"""

from nexus.profiles.detector import (
    ProfileDetector,
    ProfileMatch,
    SignalEvaluator,
)
from nexus.profiles.loader import BuiltInProfiles, load_builtin_profiles
from nexus.profiles.model import (
    DetectionSignal,
    LoadedProfile,
    ProfileModulesConvention,
    ProfileYaml,
)

__all__ = [
    "BuiltInProfiles",
    "DetectionSignal",
    "LoadedProfile",
    "ProfileDetector",
    "ProfileMatch",
    "ProfileModulesConvention",
    "ProfileYaml",
    "SignalEvaluator",
    "load_builtin_profiles",
]
