"""Phase 3 end-to-end smoke test.

Drives the full indexing pipeline against a real Laravel project
fixture: runs the PHP extractor (via docker exec into an already-
running container - see ``internal_docs/STATUS.md`` for the setup),
builds the graph, chunks the source, and (optionally) embeds with
fastembed. Writes everything to a fresh ``~/.nexus/projects/<slug>/``
under a temp directory so it doesn't collide with anything the
user already has.

Path translation
================

When the extractor runs inside docker, the reflection document
contains container-relative paths like ``/var/www/app/...``. The
chunker, on the other hand, runs on the host and needs host paths
like ``/home/user/projects/demoapp/.../app/...``. The
smoketest rewrites the paths by string replacement after the
extractor runs but before the graph/chunk passes execute. This is
fine for a smoke test; production (Phase 5) will either run the
full pipeline inside the container or use a proper path mapping
via project config.

Usage:

    .venv/bin/python examples/phase3_smoketest.py                 # no embedding
    .venv/bin/python examples/phase3_smoketest.py --with-embeddings

The embedded variant downloads the fastembed model on first run.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nexus.adapters.embedders import FastembedEmbedder, OllamaEmbedder
from nexus.adapters.embedders.cache import EmbeddingCache
from nexus.adapters.extractor import PhpExtractor
from nexus.adapters.extractor.errors import ExtractorError
from nexus.adapters.extractor.php_subprocess import ExtractorResult
from nexus.adapters.storage import ProjectStorage
from nexus.logging import configure_logging
from nexus.pipeline import PipelineContext, build_default_pipeline
from nexus.pipeline.progress import LoggingProgressReporter

if TYPE_CHECKING:
    from nexus.core.protocols import Embedder


@dataclass(frozen=True)
class EmptyProfile:
    """Structural stub for the graph builder."""

    name: str = "smoketest"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


class DockerExecExtractor:
    """PhpExtractor-shaped adapter that execs PHP inside a docker container.

    DemoApp (and CRM, and largeapp) run their PHP inside docker, so
    we can't call the host ``php`` binary directly for the smoketest.
    This stub runs the command via ``docker exec``, copies the
    resulting reflection.json out, and rewrites container paths to
    host paths so the chunker (running on the host) can read the
    source files.
    """

    def __init__(
        self,
        *,
        container: str,
        mounted_project_path: str,
        host_project_path: Path,
    ) -> None:
        self._container = container
        self._mounted = mounted_project_path.rstrip("/")
        self._host = str(host_project_path).rstrip("/")

    def extract(
        self,
        project_path: Path,
        *,
        output_path: Path,
    ) -> ExtractorResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write inside the container, then copy out via docker cp.
        container_output = f"{self._mounted}/storage/app/nexus/phase3-smoketest.json"

        cmd = [
            "docker",
            "exec",
            "-w",
            self._mounted,
            self._container,
            "php",
            "artisan",
            "nexus:extract",
            "--output",
            container_output,
            "--quiet-progress",
        ]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ExtractorError(
                f"docker exec extractor failed: {completed.stderr}",
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
            )

        # Copy the file out.
        cp_cmd = [
            "docker",
            "cp",
            f"{self._container}:{container_output}",
            str(output_path),
        ]
        cp = subprocess.run(cp_cmd, capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            raise ExtractorError(
                f"docker cp failed: {cp.stderr}",
                stdout=cp.stdout,
                stderr=cp.stderr,
                exit_code=cp.returncode,
            )

        # Rewrite container paths to host paths so the chunker can
        # read the source files.
        self._rewrite_paths(output_path)

        return ExtractorResult(
            output_path=output_path,
            exit_code=0,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _rewrite_paths(self, json_path: Path) -> None:
        raw = json_path.read_text(encoding="utf-8")
        rewritten = raw.replace(self._mounted, self._host)
        json_path.write_text(rewritten, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 3 pipeline smoke test")
    parser.add_argument(
        "--project",
        default="/home/user/projects/demoapp/api.demoapp.test/main",
        help="Host path to the Laravel project (for chunking)",
    )
    parser.add_argument(
        "--container",
        default="demoapp",
        help="Docker container to exec PHP in",
    )
    parser.add_argument(
        "--mounted-path",
        default="/var/www",
        help="Project path inside the container",
    )
    parser.add_argument(
        "--embedder",
        choices=("none", "fastembed", "ollama"),
        default="none",
        help=(
            "Which embedder backend to use. "
            "'none' runs the graph + chunk passes without embeddings. "
            "'fastembed' uses the local ONNX backend (slow on CPU). "
            "'ollama' uses a local Ollama daemon (requires `ollama serve`). "
            "Default: none."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default="nomic-embed-text",
        help="Ollama model name (when --embedder=ollama). Default: nomic-embed-text",
    )
    parser.add_argument(
        "--with-embeddings",
        action="store_true",
        help="Deprecated alias for --embedder fastembed (kept for earlier runs).",
    )
    args = parser.parse_args()

    configure_logging(fmt="console")

    project_path = Path(args.project)
    if not project_path.is_dir():
        print(f"ERROR: project path {project_path} does not exist", file=sys.stderr)
        return 1

    workspace = Path(tempfile.mkdtemp(prefix="nexus-phase3-"))
    try:
        storage = ProjectStorage(root=workspace / ".nexus", slug="demoapp-smoketest")
        cache = EmbeddingCache(root=workspace / ".nexus" / "cache" / "embeddings")

        embedder_choice = args.embedder
        if args.with_embeddings and embedder_choice == "none":
            embedder_choice = "fastembed"

        embedder: Embedder | None = None
        if embedder_choice == "fastembed":
            print("Constructing fastembed embedder (model load deferred to first embed call)")
            embedder = FastembedEmbedder()
        elif embedder_choice == "ollama":
            print(f"Constructing Ollama embedder (model={args.ollama_model})")
            embedder = OllamaEmbedder(model=args.ollama_model)

        extractor: object
        if shutil.which("docker") is not None:
            print(f"Using docker exec extractor against container '{args.container}'")
            extractor = DockerExecExtractor(
                container=args.container,
                mounted_project_path=args.mounted_path,
                host_project_path=project_path,
            )
        else:
            print("docker not found, falling back to host PHP")
            extractor = PhpExtractor()

        ctx = PipelineContext(
            project_path=project_path,
            storage=storage,
            profile=EmptyProfile(),
            embedder=embedder,  # type: ignore[arg-type]
            progress=LoggingProgressReporter(),
            include_tests=False,
        )

        pipeline = build_default_pipeline(
            extractor=extractor,  # type: ignore[arg-type]
            cache=cache,
        )

        print(f"\nRunning pipeline against {project_path}...")
        started = time.perf_counter()
        result = pipeline.run(ctx)
        elapsed = time.perf_counter() - started

        print(f"\nPipeline finished in {elapsed:.2f}s, ok={result.ok}")
        print(f"Passes run: {', '.join(result.passes_run)}")
        for pass_name, ms in result.pass_durations_ms.items():
            print(f"  {pass_name:20} {ms:8.1f} ms")

        if not result.ok:
            print("\nERRORS:")
            for err in ctx.errors:
                print(f"  [{err.code}] {err.message}")
            return 1

        if ctx.graph is not None:
            print(f"\nGraph: {len(ctx.graph.nodes)} nodes, {len(ctx.graph.edges)} edges")
        print(f"Chunks: {len(ctx.chunks)}")
        print(f"Warnings: {len(ctx.warnings)}")

        meta = ctx.storage.read_meta()
        if meta is not None:
            print("\nPersisted meta:")
            print(f"  slug           : {meta.project_slug}")
            print(f"  laravel version: {meta.laravel_version}")
            print(f"  nodes          : {meta.node_count}")
            print(f"  edges          : {meta.edge_count}")
            print(f"  embedder       : {meta.embedder_id or '<none>'}")
            print(f"  indexed_at     : {meta.indexed_at}")

        print(f"\nArtefacts under: {workspace / '.nexus' / 'projects' / 'demoapp-smoketest'}")
        return 0
    finally:
        # Keep the workspace around so the user can inspect the
        # written files. Uncomment to clean up:
        # shutil.rmtree(workspace, ignore_errors=True)
        pass


if __name__ == "__main__":
    raise SystemExit(main())
