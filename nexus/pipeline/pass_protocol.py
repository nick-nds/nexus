"""The :class:`Pass` protocol.

A pipeline pass is any object with a ``name`` attribute and a
``run(ctx)`` method. That's the entire contract. Concrete passes live
in :mod:`nexus.pipeline.passes` and implement it as regular classes
so they can take dependencies via ``__init__``.

Error handling convention
=========================

A pass signals problems in two ways:

* **Non-fatal problems** — add to ``ctx.warnings`` and continue.
* **Fatal problems** — add to ``ctx.errors``. The orchestrator checks
  ``ctx.ok()`` between passes and stops the run at the first
  failure.

Raising an exception from inside ``run`` is reserved for programmer
errors (the kind that should cause a test to fail). Runtime failures
a user could hit must be turned into structured errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from nexus.pipeline.context import PipelineContext


class Pass(Protocol):
    """The shape every pipeline pass satisfies."""

    @property
    def name(self) -> str:
        """Short identifier used in progress events and logs."""
        ...

    def run(self, ctx: PipelineContext) -> None:
        """Execute the pass, mutating the context in place."""
        ...
