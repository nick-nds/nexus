"""``nexus init`` - create or update ``nexus.yml`` in a project directory.

Interactive flow (default)
==========================

1. Detect the project slug from the directory name; offer it as the
   default, let the user override.
2. Run :class:`~nexus.profiles.ProfileDetector` on the directory; show
   the top match and ask the user to confirm or pick another.
3. Ask which embedder backend to use; default to ``fastembed`` (the
   built-in local option, no API key required).
4. Write ``nexus.yml`` with the collected values and print next-steps.

Non-interactive mode (``--non-interactive`` or ``--yes``)
==========================================================

Uses auto-detected values throughout - no prompts - and writes the
file unconditionally unless ``--no-overwrite`` is passed.  Suitable
for ``git hooks``, CI bootstrapping, and ``cookiecutter``-style
project generators.

Decision reference: ``internal_docs/PHASE-5-interface-layer.md`` §D5.6.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext


# ---------------------------------------------------------------------------
# Supported embedder providers exposed to the user
# ---------------------------------------------------------------------------

_EMBEDDER_CHOICES = ["fastembed", "ollama", "voyage", "openai"]
_DEFAULT_EMBEDDER = "fastembed"

#: Default model written to config.yml per provider, so a fresh `init`
#: produces a config that resolves to a real, installed-on-demand model.
_DEFAULT_MODELS = {
    "fastembed": "BAAI/bge-small-en-v1.5",
    "ollama": "nomic-embed-text",
    "voyage": "voyage-code-3",
    "openai": "text-embedding-3-small",
}

# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command(
    "init",
    help="Create nexus.yml in the project directory.",
)
@click.option(
    "--project-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root to initialise. Defaults to the current directory.",
)
@click.option(
    "--slug",
    "project_slug",
    default=None,
    help="Project slug. Defaults to a slugified version of the directory name.",
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="Built-in profile name to pin. Auto-detected when omitted.",
)
@click.option(
    "--embedder",
    "embedder_provider",
    type=click.Choice(_EMBEDDER_CHOICES),
    default=None,
    help=f"Embedder backend ({', '.join(_EMBEDDER_CHOICES)}). Defaults to fastembed.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Skip all prompts; use auto-detected / default values.",
)
@click.option(
    "--overwrite/--no-overwrite",
    default=True,
    help="Whether to overwrite an existing nexus.yml. Default: overwrite.",
)
@click.pass_obj
def init_command(
    cli_ctx: CliContext,
    project_path: Path | None,
    project_slug: str | None,
    profile_name: str | None,
    embedder_provider: str | None,
    non_interactive: bool,
    overwrite: bool,
) -> None:
    """Interactive wizard that creates ``nexus.yml``."""
    path = (project_path or cli_ctx.project_path).resolve()

    # Non-interactive mode is also triggered by the global --yes flag.
    skip_prompts = non_interactive or cli_ctx.yes

    # ------------------------------------------------------------------ slug
    detected_slug = _slugify(path.name)
    if project_slug is None:
        if skip_prompts:
            project_slug = detected_slug
        else:
            project_slug = click.prompt("Project slug", default=detected_slug)

    # ----------------------------------------------------------------- profile
    detected_profile = _detect_profile_name(path)
    if profile_name is None:
        if skip_prompts:
            profile_name = detected_profile
        else:
            profile_name = click.prompt(
                "Profile",
                default=detected_profile,
                type=click.Choice(_builtin_profile_names(), case_sensitive=False),
                show_choices=False,
                prompt_suffix=f" [detected: {detected_profile}]? ",
            )

    # ---------------------------------------------------------------- embedder
    if embedder_provider is None:
        if skip_prompts:
            embedder_provider = _DEFAULT_EMBEDDER
        else:
            embedder_provider = click.prompt(
                "Embedder",
                default=_DEFAULT_EMBEDDER,
                type=click.Choice(_EMBEDDER_CHOICES, case_sensitive=False),
                show_choices=True,
            )

    # ------------------------------------------------------------------ write
    nexus_yml = path / "nexus.yml"
    if nexus_yml.exists() and not overwrite:
        render(
            cli_ctx,
            {
                "status": "skipped",
                "path": str(nexus_yml),
                "reason": "nexus.yml already exists (--no-overwrite)",
            },
        )
        return

    try:
        _write_nexus_yml(nexus_yml, project_slug, profile_name, embedder_provider)
        config_path, seeded = _seed_global_embedder(cli_ctx.storage_root, embedder_provider)
    except OSError as exc:
        print_error(cli_ctx, f"could not write project files: {exc}")
        raise click.exceptions.Exit(1) from exc

    render(
        cli_ctx,
        {
            "status": "created",
            "path": str(nexus_yml),
            "slug": project_slug,
            "profile": profile_name,
            "embedder": embedder_provider,
            "config": str(config_path),
            "global_embedder": "seeded" if seeded else "preserved",
        },
    )

    if not skip_prompts:
        click.echo(
            "\nNext steps:\n"
            "  1. Review nexus.yml and adjust any settings.\n"
            "  2. Run `nexus index rebuild` to build the index.\n"
            '  3. Run `nexus ask "<question>"` to query your codebase.\n'
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert a directory name to a safe project slug.

    Lowercases, replaces non-alphanumeric runs with dashes, strips
    leading/trailing dashes.  Examples::

        "MyProject"       → "myproject"
        "my_project"      → "my-project"
        "My Cool Project!" → "my-cool-project"
    """
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "project"


def _detect_profile_name(project_path: Path) -> str:
    """Return the best-matching built-in profile name for *project_path*."""
    from nexus.profiles import ProfileDetector, load_builtin_profiles  # noqa: PLC0415

    builtins = load_builtin_profiles()
    detector = ProfileDetector(builtins=builtins)
    matches = detector.detect(project_path)
    if matches and matches[0].score > 0:
        return matches[0].profile.name
    # Guaranteed to exist since load_builtin_profiles always ships profiles.
    return next(iter(builtins)).name


def _builtin_profile_names() -> list[str]:
    """Return the names of every shipped built-in profile."""
    from nexus.profiles import load_builtin_profiles  # noqa: PLC0415

    return [p.name for p in load_builtin_profiles()]


def _write_nexus_yml(
    path: Path,
    slug: str,
    profile: str,
    embedder_provider: str,
) -> None:
    """Serialise and write ``nexus.yml`` to *path*.

    We hand-roll the YAML rather than dumping a Pydantic model so the
    output is minimal and readable (no ``null`` fields, no long lists of
    defaults). A minimal ``nexus.yml`` is easy to understand and edit.

    The embedder chosen during ``init`` is a *per-project* preference, so
    it is written here - the project ``nexus.yml`` ``embedder:`` block
    overrides the global ``~/.nexus/config.yml`` default (see
    :func:`_seed_global_embedder` and ``nexus.interfaces.cli.embedder``).
    """
    lines = [
        "schema_version: '1.0'",
        "",
        "project:",
        f"  slug: {slug}",
        "",
        f"profile: {profile}",
        "",
        "embedder:",
        f"  provider: {embedder_provider}",
        f"  model: {_DEFAULT_MODELS[embedder_provider]}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _seed_global_embedder(storage_root: Path, provider: str) -> tuple[Path, bool]:
    """Seed a machine-wide default embedder into ``<storage_root>/config.yml``.

    The per-project embedder choice is written to the project's ``nexus.yml``
    (see :func:`_write_nexus_yml`). This function only ensures a *machine*
    default exists, so that ``nexus package index`` and projects without an
    ``embedder:`` block still resolve a backend.

    An embedder already configured in the global ``config.yml`` is left
    untouched - ``init`` never clobbers a user's machine-wide preference.
    Returns ``(config_path, seeded)`` where ``seeded`` is ``True`` when the
    embedder block was written and ``False`` when an existing one was kept.
    """
    import yaml  # noqa: PLC0415

    config_path = storage_root / "config.yml"
    raw: dict[str, object] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    if "embedder" in raw:
        return config_path, False

    storage_root.mkdir(parents=True, exist_ok=True)
    raw.setdefault("schema_version", "1.0")
    raw["embedder"] = {"provider": provider, "model": _DEFAULT_MODELS[provider]}
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path, True
