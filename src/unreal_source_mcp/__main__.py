"""Entry point for `python -m unreal_source_mcp` and `uvx unreal-source-mcp`."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from unreal_source_mcp import __version__


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="unreal-source-mcp",
        description="Deep Unreal Engine source intelligence for AI agents.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--index", action="store_true",
        help="Index UE source and exit (requires UE_SOURCE_PATH env var).",
    )
    parser.add_argument(
        "--reindex", action="store_true",
        help="Delete existing DB and re-index from scratch.",
    )
    args = parser.parse_args()

    if args.index or args.reindex:
        _run_index(reindex=args.reindex)
    else:
        _run_server()


def _run_index(*, reindex: bool = False) -> None:
    from unreal_source_mcp.config import get_db_path, UE_SOURCE_PATH, UE_SHADER_PATH
    from unreal_source_mcp.db.schema import init_db
    from unreal_source_mcp.indexer.pipeline import IndexingPipeline

    if not UE_SOURCE_PATH:
        print("Error: UE_SOURCE_PATH environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    source_path = Path(UE_SOURCE_PATH)
    if not source_path.is_dir():
        print(f"Error: UE_SOURCE_PATH does not exist: {source_path}", file=sys.stderr)
        sys.exit(1)

    db_path = get_db_path()

    from unreal_source_mcp.config import _detect_version
    print(f"Detected UE version: {_detect_version()}", file=sys.stderr)

    if reindex and db_path.exists():
        print(f"Removing existing database: {db_path}", file=sys.stderr)
        db_path.unlink()

    if db_path.exists() and not reindex:
        print(f"Database already exists: {db_path}", file=sys.stderr)
        print("Use --reindex to rebuild from scratch.", file=sys.stderr)
        sys.exit(0)

    print(f"Indexing UE source from {source_path}...", file=sys.stderr)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    pipeline = IndexingPipeline(conn)
    shader_path = Path(UE_SHADER_PATH) if UE_SHADER_PATH else None
    stats = pipeline.index_engine(source_path, shader_path=shader_path)

    conn.close()

    print(
        f"Done. {stats['files_processed']} files, "
        f"{stats['symbols_extracted']} symbols, "
        f"{stats['errors']} errors.",
        file=sys.stderr,
    )
    print(f"Database: {db_path}", file=sys.stderr)


def _run_server() -> None:
    from unreal_source_mcp.server import main
    main()


if __name__ == "__main__":
    cli()
