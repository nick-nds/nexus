"""Built-in profile auto-detection.

Given a project directory and a set of built-in profiles, the detector
walks each profile's detection signals, evaluates them against the
project tree, and returns a ranked :class:`ProfileMatch` list.

The detector is intentionally cheap - it only touches the filesystem
and ``composer.json``. No PHP invocation, no class loading, no
container boot. The user can be shown a ranked profile list in a few
hundred milliseconds even on the largeapp-scale projects.

Signal kinds supported in v1:

* ``path_exists`` - the project contains a directory matching a glob.
  Used to detect DDD module layouts, action-based Laravel projects, etc.
* ``composer_requires`` - the project's ``composer.json`` pulls in a
  specific package (e.g. ``filament/filament``).
* ``class_suffix_frequency`` - at least N files in the ``app/`` tree
  end with a given suffix (e.g. ``Handler``).
* ``interface_usage`` - at least one PHP file contains an
  ``implements <Interface>`` reference. Cheap grep-style check,
  deliberately not AST-based.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 - runtime dataclass field type
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nexus.profiles.loader import BuiltInProfiles
    from nexus.profiles.model import DetectionSignal, LoadedProfile


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    """One profile's score against the project being detected.

    Attributes:
        profile: The built-in profile the score applies to.
        score: Percentage of signal weight earned, 0-100.
        earned_weight: Sum of weights of signals that fired.
        total_weight: Sum of weights of all signals in the profile.
        fired: Per-signal fire/no-fire breakdown, useful for debugging.
    """

    profile: LoadedProfile
    score: float
    earned_weight: int
    total_weight: int
    fired: dict[str, bool]


@dataclass(slots=True)
class SignalEvaluator:
    """Evaluates detection signals against a project directory.

    Split out from :class:`ProfileDetector` so callers that want to
    test one signal in isolation don't have to build a full profile.
    """

    project_path: Path
    # Cached inputs - computed lazily on first access.
    _composer: dict[str, object] | None = None
    _app_files: list[Path] | None = None

    def evaluate(self, signal: DetectionSignal) -> bool:
        """Return True if the signal fires against the project."""
        match signal.kind:
            case "path_exists":
                return self._path_exists(signal)
            case "composer_requires":
                return self._composer_requires(signal)
            case "class_suffix_frequency":
                return self._class_suffix_frequency(signal)
            case "interface_usage":
                return self._interface_usage(signal)

    def _path_exists(self, signal: DetectionSignal) -> bool:
        if signal.path is None:
            return False
        # Support glob patterns; a single match is enough.
        return any(self.project_path.glob(signal.path))

    def _composer_requires(self, signal: DetectionSignal) -> bool:
        if signal.package is None:
            return False
        if self._composer is None:
            composer_path = self.project_path / "composer.json"
            if not composer_path.is_file():
                self._composer = {}
            else:
                try:
                    self._composer = json.loads(composer_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    self._composer = {}

        for key in ("require", "require-dev"):
            section = self._composer.get(key)
            if isinstance(section, dict) and signal.package in section:
                return True
        return False

    def _class_suffix_frequency(self, signal: DetectionSignal) -> bool:
        if signal.suffix is None or signal.threshold is None:
            return False

        if self._app_files is None:
            app_dir = self.project_path / "app"
            self._app_files = list(app_dir.rglob("*.php")) if app_dir.is_dir() else []

        suffix = signal.suffix
        count = sum(1 for f in self._app_files if f.stem.endswith(suffix))
        return count >= signal.threshold

    def _interface_usage(self, signal: DetectionSignal) -> bool:
        if signal.interface is None:
            return False

        if self._app_files is None:
            app_dir = self.project_path / "app"
            self._app_files = list(app_dir.rglob("*.php")) if app_dir.is_dir() else []

        pattern = re.compile(r"implements\s+(?:[\w\\,\s]+,\s*)?" + re.escape(signal.interface))
        for file in self._app_files:
            try:
                if pattern.search(file.read_text(encoding="utf-8", errors="ignore")):
                    return True
            except OSError:
                continue
        return False


@dataclass(slots=True)
class ProfileDetector:
    """Scores every built-in profile against a project tree.

    The detector instantiates one :class:`SignalEvaluator` per project
    so file reads are cached across signals (a profile with several
    ``class_suffix_frequency`` signals reads ``app/`` once).
    """

    builtins: BuiltInProfiles

    def detect(self, project_path: Path) -> list[ProfileMatch]:
        """Return profile matches for ``project_path``, sorted by score.

        Ties are broken by profile name (ascending) so the output is
        deterministic across runs and machines.
        """
        evaluator = SignalEvaluator(project_path=project_path)
        matches: list[ProfileMatch] = []

        for profile in self.builtins:
            total = sum(signal.weight for signal in profile.signals)
            earned = 0
            fired: dict[str, bool] = {}

            for signal in profile.signals:
                key = f"{signal.kind}:{self._signal_key(signal)}"
                if evaluator.evaluate(signal):
                    earned += signal.weight
                    fired[key] = True
                else:
                    fired[key] = False

            score = (earned / total * 100) if total > 0 else 0.0
            matches.append(
                ProfileMatch(
                    profile=profile,
                    score=score,
                    earned_weight=earned,
                    total_weight=total,
                    fired=fired,
                ),
            )

        matches.sort(key=lambda m: (-m.score, m.profile.name))
        return matches

    @staticmethod
    def _signal_key(signal: DetectionSignal) -> str:
        """Build a short identifier for a signal, for the fired map."""
        return signal.path or signal.package or signal.suffix or signal.interface or "<anon>"
