"""Single source of truth for the Nexus package version.

The version is read by ``pyproject.toml`` build tooling and by the CLI's
``--version`` flag (added in Phase 5). Bump only via the release process
described in ``internal_docs/MASTER-PLAN.md``.
"""

__version__ = "1.1.0"
