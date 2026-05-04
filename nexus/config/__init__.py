"""Pydantic configuration models for Nexus.

Two YAML files govern a Nexus run:

* ``~/.nexus/config.yml`` — per-user global defaults (preferred embedder,
  API keys, response-budget thresholds). Modelled by
  :class:`~nexus.config.global_config.GlobalConfig`. See
  ``internal_docs/11-profile-system.md`` §"What lives in ~/.nexus/".
* ``./nexus.yml`` — project-level profile overrides, committed to git.
  Modelled by :class:`~nexus.config.project_profile.ProjectProfile`.
  See ``internal_docs/11-profile-system.md`` §"Profile file location".

Both files carry a ``schema_version`` field; the loaders reject documents
whose major version does not match the one this Python build speaks.

This package only defines the shapes and the loaders. Auto-detection,
built-in profiles, and profile scoring live in :mod:`nexus.profiles`.
"""

from nexus.config.global_config import (
    GLOBAL_CONFIG_SCHEMA_MAJOR,
    EmbedderConfig,
    GlobalConfig,
    load_global_config,
)
from nexus.config.loader import ConfigError, ConfigVersionError
from nexus.config.project_profile import (
    PROJECT_PROFILE_SCHEMA_MAJOR,
    IndexingSettings,
    ModulesConvention,
    ProjectProfile,
    ProjectProfileConventions,
    load_project_profile,
)

__all__ = [
    "GLOBAL_CONFIG_SCHEMA_MAJOR",
    "PROJECT_PROFILE_SCHEMA_MAJOR",
    "ConfigError",
    "ConfigVersionError",
    "EmbedderConfig",
    "GlobalConfig",
    "IndexingSettings",
    "ModulesConvention",
    "ProjectProfile",
    "ProjectProfileConventions",
    "load_global_config",
    "load_project_profile",
]
