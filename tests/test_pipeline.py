"""Tests for the IndexingPipeline."""

import sqlite3

import pytest
from pathlib import Path

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.db.queries import (
    get_symbol_by_name,
    search_symbols_fts,
    search_source_fts,
)
from unreal_source_mcp.indexer.pipeline import IndexingPipeline

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


@pytest.fixture
def indexed_db(db):
    """Return a DB that has already been indexed with the fixtures."""
    pipeline = IndexingPipeline(db)
    pipeline.index_directory(FIXTURES, module_name="SampleModule")
    return db


@pytest.fixture
def stats(db):
    """Return the stats dict from indexing fixtures."""
    pipeline = IndexingPipeline(db)
    return pipeline.index_directory(FIXTURES, module_name="SampleModule")


def test_pipeline_indexes_fixtures(stats):
    """Stats show files_processed > 0 and symbols_extracted > 0."""
    assert stats["files_processed"] > 0
    assert stats["symbols_extracted"] > 0
    assert "errors" in stats


def test_pipeline_indexes_class(indexed_db):
    """get_symbol_by_name('ASampleActor') returns a class."""
    sym = get_symbol_by_name(indexed_db, "ASampleActor")
    assert sym is not None
    assert sym["kind"] == "class"


def test_pipeline_indexes_methods(indexed_db):
    """Search for 'DoSomething' finds results."""
    results = search_symbols_fts(indexed_db, "DoSomething")
    assert len(results) > 0
    names = [r["name"] for r in results]
    assert any("DoSomething" in n for n in names)


def test_pipeline_indexes_shaders(indexed_db):
    """Search for 'EncodeNormal' finds results."""
    results = search_symbols_fts(indexed_db, "EncodeNormal")
    assert len(results) > 0


def test_pipeline_populates_source_fts(indexed_db):
    """FTS match on 'bone' returns results (from 'FBoneIndexType' or similar source)."""
    # The fixture has "Health" in source lines — search for that instead
    results = search_source_fts(indexed_db, "Health")
    assert len(results) > 0


def test_pipeline_records_inheritance(db):
    """Test that inheritance tracking works when both child and parent exist as symbols.

    ASampleActor inherits from AActor, but AActor isn't in our fixtures as a symbol.
    So we verify the internal tracking mechanism works by checking that if we manually
    add a parent symbol, inheritance is resolved.
    """
    from unreal_source_mcp.db.queries import insert_symbol, insert_module, insert_file, get_inheritance_parents

    # First index the fixtures normally
    pipeline = IndexingPipeline(db)
    pipeline.index_directory(FIXTURES, module_name="SampleModule")

    # Now manually insert AActor as a symbol so inheritance can be resolved
    mod_id = insert_module(db, name="EngineStub", path="/stub", module_type="Runtime")
    file_id = insert_file(db, path="/stub/Actor.h", module_id=mod_id, file_type="header", line_count=1)
    actor_id = insert_symbol(
        db, name="AActor", qualified_name="AActor", kind="class",
        file_id=file_id, line_start=1, line_end=1,
        parent_symbol_id=None, access=None, signature="class AActor",
        docstring=None, is_ue_macro=0,
    )

    # Re-run with a fresh pipeline that can resolve inheritance
    pipeline2 = IndexingPipeline(db)
    # Manually populate the name→id maps and bases tracking
    pipeline2._symbol_name_to_id["AActor"] = actor_id
    pipeline2._class_name_to_id["AActor"] = actor_id

    # Get ASampleActor ID
    sample = get_symbol_by_name(db, "ASampleActor")
    assert sample is not None
    pipeline2._symbol_name_to_id["ASampleActor"] = sample["id"]
    pipeline2._class_name_to_id["ASampleActor"] = sample["id"]
    pipeline2._symbol_name_to_id["_bases_ASampleActor"] = ["AActor"]

    pipeline2._resolve_inheritance()

    parents = get_inheritance_parents(db, sample["id"])
    assert len(parents) > 0
    assert parents[0]["name"] == "AActor"


def test_pipeline_indexes_includes(indexed_db):
    """includes table has entries."""
    rows = indexed_db.execute("SELECT * FROM includes").fetchall()
    assert len(rows) > 0


def test_pipeline_qualified_names(indexed_db):
    """Methods have qualified names like 'ASampleActor::DoSomething'."""
    results = search_symbols_fts(indexed_db, "DoSomething")
    qualified_names = [r["qualified_name"] for r in results]
    assert any("ASampleActor::DoSomething" in qn for qn in qualified_names)
