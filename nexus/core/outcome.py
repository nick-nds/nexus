"""Result/Outcome types for partial-failure operations.

The Nexus pipeline routinely runs steps that can partially fail (an
extractor producing a JSON document with a few warnings, a graph builder
encountering one unmappable class out of a thousand, an embedder timing
out on one chunk of many). Two design rules govern how those failures
flow through the system:

1. **Errors are values where it matters.** Pipeline-level "this happened"
   information rides on a typed result, not on exceptions. Exceptions are
   reserved for programmer errors and infrastructure failures (a SQLite
   file we can't open, a missing required argument). This is the
   "errors as values" principle from ``CLAUDE.md``.
2. **Warnings never abort the run.** A run that produced 980 useful nodes
   and twenty warnings is more useful than a run that produced nothing
   because of those twenty warnings. Code that wants to fail loudly can
   inspect ``.errors`` and decide.

The :class:`Outcome` type is the small, immutable carrier those rules use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Warning:
    """A non-fatal problem worth surfacing to the user.

    Warnings are deduplicated by neither the producer nor the carrier; if
    a builder emits the same warning twice it will appear twice. Callers
    that want unique reporting can deduplicate downstream.

    Attributes:
        code: A short, stable identifier (``snake_case``) used by tests
            and tooling. Bump only via deliberate decision; UI text can
            change freely but the code is part of the contract.
        message: A human-readable description, ideally short and concrete.
        context: Optional structured payload for tools that want to
            machine-read the warning. Keys are arbitrary; values must be
            JSON-serialisable.
    """

    code: str
    message: str
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Error:
    """A fatal problem that prevented an operation from completing.

    Errors are intentionally distinct from warnings even though they
    share a similar shape: code, message, optional context. The semantic
    difference matters at the call site - an :class:`Outcome` carrying any
    error must be treated as an unsuccessful result even if its ``value``
    field is populated.

    Attributes:
        code: A short, stable identifier.
        message: A human-readable description.
        context: Optional structured payload.
    """

    code: str
    message: str
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Outcome(Generic[T]):
    """A typed carrier for "the value plus what went wrong producing it".

    An :class:`Outcome` always has a ``value`` (which may be a placeholder
    or zero-valued type when nothing useful was produced) plus zero or
    more warnings and zero or more errors. Code that wants the simple
    "did this succeed" check should call :attr:`ok`.

    Why a single dataclass instead of separate Success/Failure variants:

    * Pipeline steps frequently want to return *both* a usable partial
      result *and* errors describing what's missing. A discriminated
      union forces an awkward "Result with warnings on the success arm,
      Result with no value on the failure arm" split that hides exactly
      the partial-success case we care about.
    * One concrete type makes the public protocols simpler - every
      pipeline step's signature reads ``Outcome[T]`` and the consumer
      always checks ``.ok``.
    * Frozen + slots keeps the runtime cost in line with a tagged union.

    Two helper constructors are provided for the common cases:

    * :meth:`success` - wrap a value with optional warnings.
    * :meth:`failure` - wrap a value with at least one error.

    Examples:
        >>> Outcome.success(42, warnings=[Warning("noop", "nothing to do")])
        Outcome(value=42, warnings=(Warning(code='noop', ...),), errors=())
        >>> Outcome.failure(0, [Error("boom", "everything is on fire")])
        Outcome(value=0, warnings=(), errors=(Error(code='boom', ...),))
    """

    value: T
    warnings: tuple[Warning, ...] = ()
    errors: tuple[Error, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the outcome is error-free.

        Warnings do not affect this; an outcome with twenty warnings and
        zero errors is still ``ok``.
        """
        return not self.errors

    def with_warning(self, warning: Warning) -> Outcome[T]:
        """Return a new outcome with one additional warning appended."""
        return Outcome(value=self.value, warnings=(*self.warnings, warning), errors=self.errors)

    def with_error(self, error: Error) -> Outcome[T]:
        """Return a new outcome with one additional error appended.

        Note this does not change the ``value`` - keep that explicit so
        partial successes remain visible.
        """
        return Outcome(value=self.value, warnings=self.warnings, errors=(*self.errors, error))

    def merge_warnings(self, other: Outcome[object]) -> Outcome[T]:
        """Return a new outcome with this value and the merged warnings/errors of both."""
        return Outcome(
            value=self.value,
            warnings=(*self.warnings, *other.warnings),
            errors=(*self.errors, *other.errors),
        )

    @classmethod
    def success(cls, value: T, warnings: list[Warning] | None = None) -> Outcome[T]:
        """Construct an error-free outcome, optionally carrying warnings."""
        return cls(value=value, warnings=tuple(warnings or ()), errors=())

    @classmethod
    def failure(cls, value: T, errors: list[Error]) -> Outcome[T]:
        """Construct an outcome with at least one error.

        The ``value`` argument is kept explicit because some failure modes
        still produce a partial value worth retaining. Use a zero-valued
        sentinel (empty list, ``None``-shaped placeholder) when no
        meaningful value exists.
        """
        if not errors:
            raise ValueError("Outcome.failure requires at least one error")
        return cls(value=value, warnings=(), errors=tuple(errors))
