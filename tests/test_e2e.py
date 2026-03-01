"""End-to-end test with actual UE Engine source (skip if not available)."""

import os
import sqlite3
import pytest
from pathlib import Path

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.indexer.pipeline import IndexingPipeline
from unreal_source_mcp.db import queries as Q

UE_SOURCE = os.environ.get("UE_SOURCE_PATH", "")

pytestmark = pytest.mark.skipif(
    not UE_SOURCE or not Path(UE_SOURCE).exists(),
    reason="UE_SOURCE_PATH not set or not found",
)


@pytest.fixture(scope="module")
def e2e_db():
    """Index a single small UE module for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    pipeline = IndexingPipeline(conn)

    # Index just CoreUObject (relatively small, always exists)
    core_path = Path(UE_SOURCE) / "Runtime" / "CoreUObject"
    if core_path.exists():
        pipeline.index_directory(core_path, module_name="CoreUObject")
    return conn


def test_e2e_finds_uobject(e2e_db):
    sym = Q.get_symbol_by_name(e2e_db, "UObject")
    assert sym is not None
    assert sym["kind"] == "class"


def test_e2e_fts_works(e2e_db):
    results = Q.search_symbols_fts(e2e_db, "UObject")
    assert len(results) > 0


def test_e2e_module_stats(e2e_db):
    stats = Q.get_module_stats(e2e_db, "CoreUObject")
    assert stats is not None
    assert stats["file_count"] > 0


def test_e2e_finds_functions(e2e_db):
    """Ensure functions are being extracted from real source."""
    results = Q.search_symbols_fts(e2e_db, "GetClass")
    assert len(results) > 0


def test_e2e_includes_extracted(e2e_db):
    """Verify includes were extracted."""
    row = e2e_db.execute("SELECT COUNT(*) AS cnt FROM includes").fetchone()
    assert row["cnt"] > 0


def test_e2e_source_fts(e2e_db):
    """Verify source FTS has content."""
    row = e2e_db.execute("SELECT COUNT(*) AS cnt FROM source_fts").fetchone()
    assert row["cnt"] > 0
