"""Pipeline-helpers for ``nexus index`` (private to the index group).

Extracted from ``index.py`` to keep that module under the project's
500-LOC ceiling. The four ``index`` subcommands (rebuild, sync,
status, clear) live in ``index.py``; everything below — pipeline
assembly, LSP resolution, cost estimation, profile detection,
progress-reporter selection, vectors-directory reset — lives here.

These helpers are CLI-only (they reach into ``CliContext``,
``click.echo``, etc.) and are not part of any public API.
"""

from __future__ import annotations

import contextlib
import shutil
import sys
from typing import TYPE_CHECKING

import click

from nexus.adapters.extractor import (
    ExtractorFailedError,
    ExtractorMissingError,
    ExtractorTimeoutError,
    PhpExtractor,
)
from nexus.adapters.storage import ProjectMeta
from nexus.interfaces.cli.output import print_error, render
from nexus.interfaces.cli.progress import JsonLinesProgressReporter, RichProgressReporter
from nexus.pipeline import Pipeline, PipelineContext
from nexus.pipeline.factory import build_default_pipeline
from nexus.profiles import ProfileDetector, load_builtin_profiles

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.core.protocols import Embedder, Lsp, Profile
    from nexus.interfaces.cli.context import CliContext
    from nexus.pipeline.progress import ProgressReporter


#: Distinct from generic errors per D5.9 — used when the user must
#: take an action (install something, change a flag) before the
#: command can succeed.
EXIT_USER_ACTION_REQUIRED = 2


def run_pipeline(
    cli_ctx: CliContext,
    *,
    project_path: Path,
    include_tests: bool,
    reset: bool,
    php_binary: str | None = None,
    container_project_path: Path | None = None,
    lsp_choice: str = "auto",
) -> None:
    """Assemble the pipeline + progress reporter and run it.

    Splits the long-lived concerns cleanly:

    * Extractor liveness check with a copy-pasteable install hint
      when the Composer package is missing (D5.9).
    * Profile auto-detection — falls back to the first built-in and
      emits a warning if no profile fires for the project.
    * LSP resolution — based on ``--lsp`` (auto/none/binary), the
      pipeline either gets an :class:`LspClient` for CALLS-edge
      enrichment or runs without it. ``auto`` with no server present
      falls back to ``None`` and emits a one-line warning.
    * Rich vs JSON-lines reporter is auto-selected via the CLI
      context's :meth:`resolved_format`.
    * Pipeline exceptions become structured CLI errors; never raise
      past this boundary.
    """
    from nexus.interfaces.cli.context import OutputFormat  # noqa: PLC0415

    storage = cli_ctx.storage()
    if reset:
        # Drop both the SQLite graph rows and the LanceDB dataset,
        # leaving the cache intact so warm reruns still benefit.
        graph_store = storage.graph()
        graph_store.clear()
        _reset_vectors_dir(storage.project_dir)

    try:
        profile = _detect_profile(project_path)
    except Exception as e:
        print_error(cli_ctx, f"profile detection failed: {e}")
        raise click.exceptions.Exit(1) from e

    lsp, lsp_server = resolve_lsp_for_run(cli_ctx, lsp_choice)
    confirm_cost_if_paid(cli_ctx, project_path)

    embedder = _build_embedder(cli_ctx)

    pipeline = _build_pipeline(php_binary=php_binary, container_project_path=container_project_path)
    pipe_ctx = PipelineContext(
        project_path=project_path,
        storage=storage,
        profile=profile,
        include_tests=include_tests,
        embedder=embedder,
        lsp=lsp,
        lsp_server=lsp_server,
    )

    reporter, manager = _resolve_reporter(cli_ctx)
    with manager:
        pipe_ctx.progress = reporter
        try:
            result = pipeline.run(pipe_ctx)
        except ExtractorMissingError as e:
            print_error(
                cli_ctx,
                f"PHP extractor not available: {e}",
                hint="install it with `composer require --dev nexus/extractor-php`",
            )
            raise click.exceptions.Exit(EXIT_USER_ACTION_REQUIRED) from e
        except ExtractorTimeoutError as e:
            print_error(
                cli_ctx,
                f"extractor timed out: {e}",
                hint="raise the timeout or prune `exclude_paths` in nexus.yml",
            )
            raise click.exceptions.Exit(1) from e
        except ExtractorFailedError as e:
            print_error(cli_ctx, f"extractor failed: {e}")
            raise click.exceptions.Exit(1) from e

    if not result.ok:
        for err in pipe_ctx.errors:
            print_error(cli_ctx, f"[{err.code}] {err.message}")
        raise click.exceptions.Exit(1)

    meta = storage.read_meta() or ProjectMeta(
        project_slug=storage.slug,
        project_path=str(project_path),
    )
    render(cli_ctx, meta)

    # The ``render`` call above already respects --format. Nothing
    # else to do on stdout.
    _ = OutputFormat  # silence the import until the TTY branch grows


def resolve_lsp_for_run(
    cli_ctx: CliContext,
    lsp_choice: str,
) -> tuple[Lsp | None, str | None]:
    """Select the LSP backend for this run from the ``--lsp`` flag.

    Returns ``(lsp_instance, lsp_server_label)``. The instance is what
    the pipeline injects into :attr:`PipelineContext.lsp`; the label
    is the descriptor recorded in ``meta.json`` so downstream
    introspection knows what enrichment ran.

    ``lsp_choice`` values:

    * ``"none"`` — disable enrichment; returns ``(None, None)``.
    * ``"auto"`` — auto-discover an LSP via :func:`resolve_lsp_binary`.
      If a server is found, returns it. If none, emits a one-line
      stderr warning and returns ``(None, None)`` so the pipeline
      still produces a structural graph.
    * ``"intelephense"`` / ``"phpactor"`` / absolute path — requested
      explicitly. If not found, exits with
      :data:`EXIT_USER_ACTION_REQUIRED` so the user can install or
      correct the path.
    """
    from nexus.adapters.lsp import LspClient, resolve_lsp_binary  # noqa: PLC0415

    _ = cli_ctx  # reserved for future per-format output routing

    choice = lsp_choice.strip().lower()
    if choice == "none":
        return None, None

    if choice == "auto":
        resolved = resolve_lsp_binary()
        if resolved is None:
            click.echo(
                "WARNING: No LSP server found (tried: intelephense, phpactor, "
                "Mason bin).\n"
                "         CALLS-edge enrichment will be skipped.\n"
                "         Install intelephense:  npm install -g intelephense\n"
                "         Install phpactor:      "
                "https://phpactor.readthedocs.io/en/master/usage/standalone.html\n"
                "         Or pass --lsp none to suppress this warning.",
                err=True,
            )
            return None, None
        binary, args = resolved
        return LspClient(binary, args), binary

    # Explicit name or path.
    resolved = resolve_lsp_binary(preferred=lsp_choice)
    if resolved is None:
        print_error(
            cli_ctx,
            f"LSP server {lsp_choice!r} not found.",
            hint="install it on PATH, or pass --lsp none to skip CALLS enrichment",
        )
        raise click.exceptions.Exit(EXIT_USER_ACTION_REQUIRED)
    binary, args = resolved
    return LspClient(binary, args), binary


# ---------------------------------------------------------------------------
# Cost estimation for paid embedder backends
# ---------------------------------------------------------------------------


#: Providers that bill per token. Local backends are excluded.
_PAID_PROVIDERS: frozenset[str] = frozenset({"voyage", "openai"})

#: Approximate cost in USD per million tokens (conservative upper bound).
_COST_PER_MILLION_TOKENS: dict[str, float] = {
    "voyage": 0.06,  # voyage-3
    "openai": 0.13,  # text-embedding-3-large (worst case)
}

#: Approximate fraction of project PHP source that becomes embedded text.
#: Chunking, deduplication, and the embedding cache all reduce this.
_EMBEDDING_FRACTION = 0.30

#: Characters per token (industry rule of thumb for code).
_CHARS_PER_TOKEN = 4.0


def confirm_cost_if_paid(cli_ctx: CliContext, project_path: Path) -> None:
    """Show a cost estimate and require confirmation for paid embedders.

    Reads the global config to determine the configured provider.  If the
    provider is in :data:`_PAID_PROVIDERS` and the estimated cost exceeds
    :attr:`~nexus.config.CostThresholds.confirm_above_usd`, the user is
    asked to confirm.  The ``--yes`` CLI flag bypasses the prompt.

    Does nothing for local/free backends (fastembed, sentence_transformers,
    ollama) or when the estimated cost is within the configured threshold.
    """
    from nexus.config.global_config import load_global_config  # noqa: PLC0415

    config_path = cli_ctx.storage_root / "config.yml"
    global_cfg = load_global_config(config_path)
    provider = global_cfg.embedder.provider

    if provider not in _PAID_PROVIDERS:
        return

    total_chars = sum(p.stat().st_size for p in project_path.rglob("*.php") if p.is_file())
    est_tokens = int(total_chars * _EMBEDDING_FRACTION / _CHARS_PER_TOKEN)
    cost_per_million = _COST_PER_MILLION_TOKENS.get(provider, 0.10)
    est_cost_usd = est_tokens / 1_000_000 * cost_per_million

    threshold = global_cfg.cost.confirm_above_usd
    if threshold > 0 and est_cost_usd < threshold:
        return  # within the automatic-approval range

    click.echo(
        f"Estimated embedding cost: ~${est_cost_usd:.4f} USD "
        f"({est_tokens:,} tokens x ${cost_per_million}/M via {provider})\n"
        f"(Threshold: ${threshold:.2f} USD — set cost.confirm_above_usd in "
        f"~/.nexus/config.yml to change)",
        err=True,
    )

    if cli_ctx.yes:
        click.echo("Proceeding (--yes was set).", err=True)
        return

    click.confirm(
        "Proceed with embedding at this estimated cost?",
        default=False,
        abort=True,
    )


# ---------------------------------------------------------------------------
# Pipeline + embedder + profile + reporter wiring
# ---------------------------------------------------------------------------


def _build_pipeline(
    php_binary: str | None = None,
    container_project_path: Path | None = None,
) -> Pipeline:
    """Build the default pipeline with a stock :class:`PhpExtractor`.

    Kept as a separate function so tests can monkeypatch a fake
    extractor without reaching into :mod:`nexus.pipeline.factory`.

    Args:
        php_binary: Forwarded to :class:`PhpExtractor`. ``None`` uses
            whichever ``php`` is on PATH. Multi-word strings such as
            ``"docker exec my-app php"`` are shell-split by the
            extractor so they work correctly as a subprocess argv list.
        container_project_path: Forwarded to :class:`PhpExtractor`.
            When the project runs in a container, this is the path
            where the project is mounted inside that container.
    """
    return build_default_pipeline(
        extractor=PhpExtractor(
            php_binary=php_binary,
            container_project_path=container_project_path,
        )
    )


def _build_embedder(cli_ctx: CliContext) -> Embedder | None:
    """Construct an embedder from the global config, or return None.

    Returns None (no embedding) if the config file doesn't exist or
    if the configured provider is unknown. Errors are printed as
    warnings rather than aborting the pipeline — a missing embedder
    degrades gracefully to graph-only mode.
    """
    from nexus.adapters.embedders.registration import register_builtin_embedders  # noqa: PLC0415
    from nexus.config.global_config import load_global_config  # noqa: PLC0415
    from nexus.plugins.registry import PluginRegistry  # noqa: PLC0415

    config_path = cli_ctx.storage_root / "config.yml"
    if not config_path.exists():
        return None

    global_cfg = load_global_config(config_path)
    registry = PluginRegistry()
    register_builtin_embedders(registry)

    embedder_cfg = global_cfg.embedder
    config_dict: dict[str, object] = {"model": embedder_cfg.model}
    if embedder_cfg.dimensions is not None:
        config_dict["dimensions"] = embedder_cfg.dimensions

    try:
        return registry.resolve_embedder(embedder_cfg.provider, config_dict)
    except KeyError as e:
        click.echo(f"WARNING: {e} — running without embedder.", file=sys.stderr)
        return None


def _detect_profile(project_path: Path) -> Profile:
    """Return the best auto-detected profile for ``project_path``.

    Falls back to the first built-in profile when detection yields
    nothing (an edge case, but we don't want the pipeline to fail on
    a directory that happens to score zero on every signal).
    """
    builtins = load_builtin_profiles()
    detector = ProfileDetector(builtins=builtins)
    matches = detector.detect(project_path)
    if matches and matches[0].score > 0:
        return matches[0].profile
    return next(iter(builtins))


def _resolve_reporter(
    cli_ctx: CliContext,
) -> tuple[ProgressReporter, contextlib.AbstractContextManager[object]]:
    """Pick a rich / JSON-lines reporter based on the CLI format flag.

    Returns both the reporter (the pipeline subscribes to this) and
    a context manager that must wrap the ``pipeline.run`` call so
    rich's live display can tear down properly. The JSON-lines
    reporter uses a no-op manager since it has no lifecycle.
    """
    from nexus.interfaces.cli.context import OutputFormat  # noqa: PLC0415

    if cli_ctx.resolved_format() == OutputFormat.PRETTY:
        reporter = RichProgressReporter()
        return reporter, reporter
    return JsonLinesProgressReporter(), contextlib.nullcontext()


def _reset_vectors_dir(project_dir: Path) -> None:
    """Remove the LanceDB dataset directory if it exists.

    Dropping the graph rows leaves the vectors referencing node ids
    that no longer exist. The cleanest rebuild story is to blow
    away the vectors directory and let LanceDB recreate it.
    """
    vectors = project_dir / "vectors"
    if vectors.exists():
        shutil.rmtree(vectors)
