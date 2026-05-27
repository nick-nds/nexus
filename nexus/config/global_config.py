"""User-level global config stored at ``~/.nexus/config.yml``.

The global config is a small set of per-user preferences (default
embedder, API keys, cost thresholds). Each field is optional so a fresh
install can run with zero configuration; the defaults are chosen to be
safe and local (no paid API calls, no surprising bills).

See ``internal_docs/11-profile-system.md`` §"What lives in ~/.nexus/"
for the design rationale - the global config is for *user preferences*,
not *project conventions*. Project conventions go in ``nexus.yml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from nexus.config.loader import (
    check_schema_major,
    load_yaml_document,
    validate_model,
)

if TYPE_CHECKING:
    from pathlib import Path

GLOBAL_CONFIG_SCHEMA_MAJOR = 1


class EmbedderConfig(BaseModel):
    """Which embedder backend to use and how to authenticate it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(
        default="sentence_transformers",
        description=(
            "Backend name: one of sentence_transformers, ollama, voyage, openai. "
            "Local defaults are used out of the box so no API key is required."
        ),
    )
    model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Model identifier within the provider.",
    )
    api_key_env: str | None = Field(
        default=None,
        description=(
            "Name of the environment variable holding the API key for paid "
            "providers. Never the key itself - we don't want keys in YAML."
        ),
    )
    dimensions: int | None = Field(
        default=None,
        description="Vector dimensionality override when the provider supports multiple.",
    )


class CostThresholds(BaseModel):
    """Dollar limits that gate paid-embedder calls without explicit confirmation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    confirm_above_usd: float = Field(
        default=0.50,
        ge=0.0,
        description=(
            "Any indexing run whose estimated cost exceeds this is blocked "
            "unless the CLI is invoked with --yes. Zero disables the gate."
        ),
    )


class AskConfig(BaseModel):
    """Tunables for ``nexus ask``'s classifier-routing layer.

    The defaults are tuned for ``nomic-embed-text`` (the recommended
    free embedder). Different embedders produce different
    ``vector_score`` distributions - for example, voyage-code-3 hits
    higher absolute scores on relevant code, OpenAI text-embedding-3
    sits a bit lower - so the floor is configurable rather than
    hardcoded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_confidence_floor: float = Field(
        default=0.65,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum vector_score for a semantic-search hit to count as "
            "a confident answer when the classifier had to fall back "
            "from a low-confidence rule. Below this, ``ask`` returns a "
            "structured ``no_confident_match`` refusal instead of weak "
            "hits. Default 0.65 fits ``nomic-embed-text``; lower it "
            "(e.g. 0.55) for embedders with tighter score distributions, "
            "or raise it for stricter refusal behaviour."
        ),
    )


class GlobalConfig(BaseModel):
    """The user's ``~/.nexus/config.yml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = f"{GLOBAL_CONFIG_SCHEMA_MAJOR}.0"
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    cost: CostThresholds = Field(default_factory=CostThresholds)
    ask: AskConfig = Field(default_factory=AskConfig)
    data_dir: str | None = Field(
        default=None,
        description=(
            "Override for ~/.nexus/. Rarely needed outside tests and multi-user machines."
        ),
    )

    @classmethod
    def defaults(cls) -> GlobalConfig:
        """Return a :class:`GlobalConfig` with every field at its default.

        The "no file found" path in :func:`load_global_config` uses this
        so Nexus is usable out of the box without requiring the user to
        create a config file at all.
        """
        return cls()


def load_global_config(path: Path) -> GlobalConfig:
    """Load ``~/.nexus/config.yml`` if present, else return defaults.

    Args:
        path: Usually ``Path.home() / ".nexus" / "config.yml"``.

    Returns:
        A fully-validated :class:`GlobalConfig`. If ``path`` doesn't
        exist, returns :meth:`GlobalConfig.defaults`.

    Raises:
        ConfigParseError: the file exists but isn't valid YAML or
            doesn't match the schema.
        ConfigVersionError: the file's ``schema_version`` major isn't
            :data:`GLOBAL_CONFIG_SCHEMA_MAJOR`.
    """
    if not path.is_file():
        return GlobalConfig.defaults()

    raw = load_yaml_document(path)
    check_schema_major(raw, expected_major=GLOBAL_CONFIG_SCHEMA_MAJOR, source=path)
    return validate_model(GlobalConfig, raw, source=path)
