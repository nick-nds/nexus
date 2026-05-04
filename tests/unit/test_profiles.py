"""Tests for nexus.profiles."""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.profiles import (
    ProfileDetector,
    SignalEvaluator,
    load_builtin_profiles,
)
from nexus.profiles.loader import BuiltInProfileError
from nexus.profiles.model import DetectionSignal

# ---------------------------------------------------------------------------
# Built-in loader
# ---------------------------------------------------------------------------


class TestBuiltInLoader:
    def test_all_seven_profiles_load(self) -> None:
        profiles = load_builtin_profiles()

        names = set(profiles.names())
        expected = {
            "laravel-default",
            "laravel-repository",
            "laravel-ddd",
            "laravel-ddd-cqrs",
            "laravel-actions",
            "laravel-filament",
            "laravel-api",
        }
        assert names == expected

    def test_ordering_is_deterministic(self) -> None:
        first = load_builtin_profiles()
        second = load_builtin_profiles()

        assert first.names() == second.names()
        # Sorted alphabetically
        assert first.names() == sorted(first.names())

    def test_ddd_cqrs_has_expected_conventions(self) -> None:
        profiles = load_builtin_profiles()
        ddd_cqrs = profiles.get("laravel-ddd-cqrs")

        assert ddd_cqrs is not None
        assert ddd_cqrs.custom_suffixes["Handler"] == "command_handler"
        assert ddd_cqrs.custom_suffixes["QueryHandler"] == "query_handler"
        assert ddd_cqrs.modules is not None
        assert "Domain" in ddd_cqrs.modules.layers

    def test_contains_works(self) -> None:
        profiles = load_builtin_profiles()
        assert "laravel-default" in profiles
        assert "nonexistent" not in profiles

    def test_get_returns_none_for_missing(self) -> None:
        assert load_builtin_profiles().get("fake-profile") is None


# ---------------------------------------------------------------------------
# Signal evaluator
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_laravel_project(tmp_path: Path) -> Path:
    """Build a fake Laravel project tree with the canonical directories."""
    (tmp_path / "app" / "Http" / "Controllers").mkdir(parents=True)
    (tmp_path / "app" / "Models").mkdir(parents=True)
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "api.php").touch()
    (tmp_path / "composer.json").write_text(
        '{"require": {"laravel/framework": "^12.0"}}',
    )
    return tmp_path


class TestPathExistsSignal:
    def test_fires_on_present_path(self, minimal_laravel_project: Path) -> None:
        evaluator = SignalEvaluator(project_path=minimal_laravel_project)
        signal = DetectionSignal(kind="path_exists", weight=10, path="app/Http/Controllers")
        assert evaluator.evaluate(signal)

    def test_does_not_fire_on_absent_path(self, minimal_laravel_project: Path) -> None:
        evaluator = SignalEvaluator(project_path=minimal_laravel_project)
        signal = DetectionSignal(kind="path_exists", weight=10, path="app/Actions")
        assert not evaluator.evaluate(signal)

    def test_glob_pattern_works(self, tmp_path: Path) -> None:
        (tmp_path / "app" / "Modules" / "CRM" / "Domain").mkdir(parents=True)
        evaluator = SignalEvaluator(project_path=tmp_path)
        signal = DetectionSignal(kind="path_exists", weight=10, path="app/Modules/*/Domain")
        assert evaluator.evaluate(signal)


class TestComposerRequiresSignal:
    def test_fires_on_declared_package(self, minimal_laravel_project: Path) -> None:
        evaluator = SignalEvaluator(project_path=minimal_laravel_project)
        signal = DetectionSignal(
            kind="composer_requires",
            weight=10,
            package="laravel/framework",
        )
        assert evaluator.evaluate(signal)

    def test_does_not_fire_on_missing_package(self, minimal_laravel_project: Path) -> None:
        evaluator = SignalEvaluator(project_path=minimal_laravel_project)
        signal = DetectionSignal(
            kind="composer_requires",
            weight=10,
            package="filament/filament",
        )
        assert not evaluator.evaluate(signal)


class TestInterfaceUsageSignal:
    def test_fires_on_implements_declaration(self, tmp_path: Path) -> None:
        app = tmp_path / "app" / "Jobs"
        app.mkdir(parents=True)
        (app / "SendEmail.php").write_text(
            "<?php\n"
            "namespace App\\Jobs;\n"
            "class SendEmail implements ShouldQueue\n"
            "{\n"
            "    public function handle(): void {}\n"
            "}\n",
        )

        evaluator = SignalEvaluator(project_path=tmp_path)
        signal = DetectionSignal(
            kind="interface_usage",
            weight=10,
            interface="ShouldQueue",
        )
        assert evaluator.evaluate(signal)

    def test_does_not_fire_on_missing_interface(self, tmp_path: Path) -> None:
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "Plain.php").write_text("<?php\nnamespace App;\nclass Plain {}\n")

        evaluator = SignalEvaluator(project_path=tmp_path)
        signal = DetectionSignal(
            kind="interface_usage",
            weight=10,
            interface="ShouldQueue",
        )
        assert not evaluator.evaluate(signal)


class TestClassSuffixFrequencySignal:
    def test_fires_above_threshold(self, tmp_path: Path) -> None:
        app = tmp_path / "app" / "Actions"
        app.mkdir(parents=True)
        for i in range(5):
            (app / f"Action{i}Action.php").write_text("<?php")

        evaluator = SignalEvaluator(project_path=tmp_path)
        signal = DetectionSignal(
            kind="class_suffix_frequency",
            weight=10,
            suffix="Action",
            threshold=3,
        )
        assert evaluator.evaluate(signal)

    def test_does_not_fire_below_threshold(self, tmp_path: Path) -> None:
        app = tmp_path / "app"
        app.mkdir(parents=True)
        (app / "OnlyOneAction.php").write_text("<?php")

        evaluator = SignalEvaluator(project_path=tmp_path)
        signal = DetectionSignal(
            kind="class_suffix_frequency",
            weight=10,
            suffix="Action",
            threshold=3,
        )
        assert not evaluator.evaluate(signal)


# ---------------------------------------------------------------------------
# Auto-detector end-to-end
# ---------------------------------------------------------------------------


class TestProfileDetector:
    def test_minimal_laravel_matches_default(self, minimal_laravel_project: Path) -> None:
        detector = ProfileDetector(builtins=load_builtin_profiles())

        matches = detector.detect(minimal_laravel_project)

        assert matches[0].profile.name == "laravel-default"
        assert matches[0].score >= 90

    def test_ddd_project_ranks_ddd_profiles_high(self, tmp_path: Path) -> None:
        # Build a DDD-shaped project tree
        (tmp_path / "app" / "Http" / "Controllers").mkdir(parents=True)
        (tmp_path / "app" / "Modules" / "CRM" / "Domain").mkdir(parents=True)
        (tmp_path / "app" / "Modules" / "CRM" / "Application").mkdir(parents=True)
        (tmp_path / "app" / "Modules" / "CRM" / "Infrastructure").mkdir(parents=True)
        (tmp_path / "composer.json").write_text('{"require": {"laravel/framework": "^12.0"}}')

        detector = ProfileDetector(builtins=load_builtin_profiles())
        matches = detector.detect(tmp_path)

        # Either laravel-ddd or laravel-ddd-cqrs should be top.
        assert matches[0].profile.name.startswith("laravel-ddd")

    def test_actions_project_ranks_actions_high(self, tmp_path: Path) -> None:
        app = tmp_path / "app" / "Actions"
        app.mkdir(parents=True)
        (tmp_path / "composer.json").write_text(
            '{"require": {"laravel/framework": "^12.0", "lorisleiva/laravel-actions": "^2.0"}}'
        )
        for i in range(5):
            (app / f"DoThing{i}Action.php").write_text("<?php")

        detector = ProfileDetector(builtins=load_builtin_profiles())
        matches = detector.detect(tmp_path)

        # Actions profile should either be top or tied with default
        top_names = [m.profile.name for m in matches if m.score >= 90]
        assert "laravel-actions" in top_names

    def test_empty_project_scores_zero(self, tmp_path: Path) -> None:
        detector = ProfileDetector(builtins=load_builtin_profiles())
        matches = detector.detect(tmp_path)

        # All seven profiles return a match entry even if none fire.
        assert len(matches) == 7
        assert all(m.score == 0.0 for m in matches)

    def test_fired_breakdown_is_included(self, minimal_laravel_project: Path) -> None:
        detector = ProfileDetector(builtins=load_builtin_profiles())
        matches = detector.detect(minimal_laravel_project)

        top = matches[0]
        # The fired map has one entry per signal in the profile.
        assert len(top.fired) == len(top.profile.signals)
        # At least one signal fired in a minimal Laravel project.
        assert any(top.fired.values())


class TestBuiltInProfileError:
    def test_it_is_exception(self) -> None:
        # Sanity check that the typed error can be raised and caught.
        with pytest.raises(BuiltInProfileError):
            raise BuiltInProfileError("test")
