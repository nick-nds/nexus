"""Adapter layer: concrete implementations of the core protocols.

Everything that performs I/O — storage backends, embedders, LSP clients,
the PHP extractor subprocess wrapper — lives here. Pure domain logic
stays in :mod:`nexus.core` and never imports from this package. The
architectural rule is enforced by a test in ``tests/architecture/``.
"""
