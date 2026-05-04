"""Pure domain logic for Nexus.

The :mod:`nexus.core` package contains every piece of code that takes data
in and returns data out without performing I/O. Storage, network calls,
subprocess invocation, and filesystem access live one layer out, in
:mod:`nexus.adapters`. The boundary is enforced by an architectural test
in ``tests/architecture/``.

The split exists so that:

* Unit tests for domain logic need no fixtures, no temp directories, and
  no network access.
* Concrete adapters can be swapped at the edges (a different vector store,
  a different graph store) without touching domain code.
* Pro-tier plugins can plug new implementations into the protocols
  defined here without depending on any concrete OSS adapter.
"""
