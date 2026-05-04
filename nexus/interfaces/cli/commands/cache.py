"""``nexus cache`` — manage the embedding cache.

Two subcommands:

* ``size``  — report the disk usage of the embedding cache directory.
* ``clear`` — delete all cached embeddings (with confirmation or --force).

The cache lives at ``<storage-root>/cache/embeddings/``. Each model gets
a sub-directory; each cached entry is a ``.json`` file keyed by a hash
of the text. Wiping the whole directory forces a cold re-embed on the
next ``nexus index rebuild`` (or sync).

See :mod:`nexus.adapters.embedders.cache` for the layout.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nexus.interfaces.cli.output import print_error, render

if TYPE_CHECKING:
    from nexus.interfaces.cli.context import CliContext

# The cache directory is always <storage-root>/cache/embeddings
_CACHE_SUBDIR = Path("cache") / "embeddings"


@click.group(
    name="cache",
    help="Inspect and clear the embedding cache.",
)
def cache_group() -> None:
    """Parent group for cache subcommands."""


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------


@cache_group.command("size", help="Show the disk usage of the embedding cache.")
@click.pass_obj
def size_command(cli_ctx: CliContext) -> None:
    """Report the byte size and entry count of the embedding cache."""
    cache_dir = cli_ctx.storage_root / _CACHE_SUBDIR

    if not cache_dir.exists():
        render(
            cli_ctx,
            {
                "cache_dir": str(cache_dir),
                "exists": False,
                "total_bytes": 0,
                "entry_count": 0,
            },
        )
        return

    total_bytes, entry_count = _measure_cache(cache_dir)
    render(
        cli_ctx,
        {
            "cache_dir": str(cache_dir),
            "exists": True,
            "total_bytes": total_bytes,
            "total_human": _human_bytes(total_bytes),
            "entry_count": entry_count,
        },
    )


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


@cache_group.command("clear", help="Delete all cached embeddings.")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt.",
)
@click.pass_obj
def clear_command(cli_ctx: CliContext, force: bool) -> None:
    """Wipe the embedding cache directory after confirmation."""
    cache_dir = cli_ctx.storage_root / _CACHE_SUBDIR

    if not cache_dir.exists():
        render(cli_ctx, {"status": "nothing to clear", "cache_dir": str(cache_dir)})
        return

    total_bytes, entry_count = _measure_cache(cache_dir)

    if not (force or cli_ctx.yes):
        click.confirm(
            f"Delete {entry_count} cached embeddings ({_human_bytes(total_bytes)}) at {cache_dir}?",
            abort=True,
        )

    try:
        shutil.rmtree(cache_dir)
    except OSError as exc:
        print_error(cli_ctx, f"could not clear cache: {exc}")
        raise click.exceptions.Exit(1) from exc

    render(
        cli_ctx,
        {
            "status": "cleared",
            "cache_dir": str(cache_dir),
            "entries_removed": entry_count,
            "bytes_freed": total_bytes,
            "bytes_freed_human": _human_bytes(total_bytes),
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _measure_cache(cache_dir: Path) -> tuple[int, int]:
    """Return ``(total_bytes, entry_count)`` for all files under *cache_dir*."""
    total_bytes = 0
    entry_count = 0
    for f in cache_dir.rglob("*.json"):
        try:
            total_bytes += f.stat().st_size
            entry_count += 1
        except OSError:
            pass
    return total_bytes, entry_count


_KiB = 1024
_MiB = _KiB * _KiB
_GiB = _MiB * _KiB


def _human_bytes(n: int) -> str:
    """Format byte count as a human-readable string (KiB / MiB / GiB)."""
    if n < _KiB:
        return f"{n} B"
    if n < _MiB:
        return f"{n / _KiB:.1f} KiB"
    if n < _GiB:
        return f"{n / _MiB:.1f} MiB"
    return f"{n / _GiB:.2f} GiB"
