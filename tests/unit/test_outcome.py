"""Tests for nexus.core.outcome."""

from __future__ import annotations

import pytest
from nexus.core.outcome import Error, Outcome, Warning


class TestSuccess:
    def test_success_is_ok(self) -> None:
        result = Outcome.success(42)

        assert result.ok
        assert result.value == 42
        assert result.warnings == ()
        assert result.errors == ()

    def test_success_with_warnings_is_still_ok(self) -> None:
        result = Outcome.success(42, warnings=[Warning("noop", "nothing happened")])

        assert result.ok
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "noop"


class TestFailure:
    def test_failure_is_not_ok(self) -> None:
        result = Outcome.failure(0, [Error("boom", "everything is on fire")])

        assert not result.ok
        assert result.value == 0
        assert len(result.errors) == 1

    def test_failure_preserves_partial_value(self) -> None:
        # Partial-success carrying both a usable value and an error
        # describing what is missing.
        result = Outcome.failure([1, 2, 3], [Error("incomplete", "missing entry 4")])

        assert result.value == [1, 2, 3]
        assert not result.ok

    def test_failure_requires_at_least_one_error(self) -> None:
        with pytest.raises(ValueError, match="at least one error"):
            Outcome.failure(0, [])


class TestCombinators:
    def test_with_warning_returns_new_instance(self) -> None:
        original = Outcome.success(1)
        updated = original.with_warning(Warning("w", "msg"))

        assert original.warnings == ()
        assert len(updated.warnings) == 1
        assert updated.value == 1
        assert updated.ok

    def test_with_error_keeps_value_but_flips_ok(self) -> None:
        original = Outcome.success(1)
        updated = original.with_error(Error("e", "msg"))

        assert updated.value == 1
        assert not updated.ok

    def test_merge_warnings_combines_both_outcomes(self) -> None:
        a = Outcome(value="a", warnings=(Warning("w1", "1"),), errors=())
        b = Outcome(value="b", warnings=(Warning("w2", "2"),), errors=(Error("e1", "1"),))

        merged = a.merge_warnings(b)

        assert merged.value == "a"  # left value wins
        assert len(merged.warnings) == 2
        assert len(merged.errors) == 1
        assert not merged.ok


class TestImmutability:
    def test_outcome_is_frozen(self) -> None:
        result = Outcome.success(1)
        with pytest.raises(AttributeError):
            result.value = 2  # type: ignore[misc]

    def test_warning_is_frozen(self) -> None:
        warning = Warning("c", "m")
        with pytest.raises(AttributeError):
            warning.code = "x"  # type: ignore[misc]
