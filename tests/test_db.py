"""Tests for database schema and query layer."""

import sqlite3
import pytest

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.db import queries


@pytest.fixture
def conn():
    """Create an in-memory SQLite database with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def populated(conn):
    """Seed the database with a module, file, and a few symbols."""
    mod_id = queries.insert_module(
        conn, name="CoreModule", path="/Engine/Source/Runtime/Core",
        module_type="Runtime", build_cs_path="/Engine/Source/Runtime/Core/Core.Build.cs",
    )
    file_id = queries.insert_file(
        conn, path="/Engine/Source/Runtime/Core/Public/Actor.h",
        module_id=mod_id, file_type="header", line_count=500, last_modified=1000.0,
    )
    parent_sym = queries.insert_symbol(
        conn, name="AActor", qualified_name="AActor", kind="class",
        file_id=file_id, line_start=10, line_end=400,
        parent_symbol_id=None, access="public",
        signature="class AActor : public UObject",
        docstring="Base actor class", is_ue_macro=0,
    )
    child_sym = queries.insert_symbol(
        conn, name="GetActorLocation", qualified_name="AActor::GetActorLocation",
        kind="function", file_id=file_id, line_start=50, line_end=55,
        parent_symbol_id=parent_sym, access="public",
        signature="FVector GetActorLocation() const",
        docstring="Returns the location of this actor", is_ue_macro=0,
    )
    return {
        "conn": conn,
        "module_id": mod_id,
        "file_id": file_id,
        "parent_sym_id": parent_sym,
        "child_sym_id": child_sym,
    }


# ─── Schema tests ───────────────────────────────────────────────────────

class TestSchema:
    def test_creates_all_tables(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        expected = {"modules", "files", "symbols", "inheritance",
                    "references", "includes", "symbols_fts",
                    "source_fts", "meta"}
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_schema_version(self, conn):
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert row is not None
        assert row[0] == "1"

    def test_fts_tables_exist(self, conn):
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "symbols_fts" in tables
        assert "source_fts" in tables


# ─── Insert + query symbol tests ────────────────────────────────────────

class TestSymbolCRUD:
    def test_insert_and_get_symbol_by_id(self, populated):
        sym = queries.get_symbol_by_id(populated["conn"], populated["parent_sym_id"])
        assert sym is not None
        assert sym["name"] == "AActor"
        assert sym["kind"] == "class"

    def test_get_symbol_by_qualified_name(self, populated):
        sym = queries.get_symbol_by_name(populated["conn"], "AActor::GetActorLocation")
        assert sym is not None
        assert sym["name"] == "GetActorLocation"

    def test_get_symbol_by_short_name(self, populated):
        sym = queries.get_symbol_by_name(populated["conn"], "AActor")
        assert sym is not None

    def test_get_symbols_by_name(self, populated):
        results = queries.get_symbols_by_name(populated["conn"], "AActor")
        assert len(results) == 1
        assert results[0]["kind"] == "class"

    def test_get_symbols_by_name_with_kind(self, populated):
        results = queries.get_symbols_by_name(populated["conn"], "AActor", kind="function")
        assert len(results) == 0

    def test_get_symbol_not_found(self, populated):
        assert queries.get_symbol_by_name(populated["conn"], "NonExistent") is None


# ─── FTS symbol search tests ────────────────────────────────────────────

class TestSymbolFTS:
    def test_fts_search_finds_symbol(self, populated):
        results = queries.search_symbols_fts(populated["conn"], "Actor")
        assert len(results) >= 1
        names = [r["name"] for r in results]
        assert "AActor" in names

    def test_fts_search_by_docstring(self, populated):
        results = queries.search_symbols_fts(populated["conn"], "location")
        assert len(results) >= 1

    def test_fts_search_empty(self, populated):
        results = queries.search_symbols_fts(populated["conn"], "zzzznonexistent")
        assert len(results) == 0


# ─── Inheritance tests ───────────────────────────────────────────────────

class TestInheritance:
    def test_insert_and_query_parents(self, populated):
        conn = populated["conn"]
        # Create a base class symbol
        base_id = queries.insert_symbol(
            conn, name="UObject", qualified_name="UObject", kind="class",
            file_id=populated["file_id"], line_start=1, line_end=100,
            parent_symbol_id=None, access="public",
            signature="class UObject", docstring="Base UObject", is_ue_macro=0,
        )
        queries.insert_inheritance(conn, child_id=populated["parent_sym_id"], parent_id=base_id)

        parents = queries.get_inheritance_parents(conn, populated["parent_sym_id"])
        assert len(parents) == 1
        assert parents[0]["name"] == "UObject"

    def test_insert_and_query_children(self, populated):
        conn = populated["conn"]
        base_id = queries.insert_symbol(
            conn, name="UObject", qualified_name="UObject", kind="class",
            file_id=populated["file_id"], line_start=1, line_end=100,
            parent_symbol_id=None, access="public",
            signature="class UObject", docstring="Base UObject", is_ue_macro=0,
        )
        queries.insert_inheritance(conn, child_id=populated["parent_sym_id"], parent_id=base_id)

        children = queries.get_inheritance_children(conn, base_id)
        assert len(children) == 1
        assert children[0]["name"] == "AActor"


# ─── References tests ───────────────────────────────────────────────────

class TestReferences:
    def test_insert_and_query_references_to(self, populated):
        conn = populated["conn"]
        # Create a caller symbol
        caller_id = queries.insert_symbol(
            conn, name="Tick", qualified_name="AMyActor::Tick", kind="function",
            file_id=populated["file_id"], line_start=200, line_end=210,
            parent_symbol_id=None, access="public",
            signature="void Tick(float)", docstring="", is_ue_macro=0,
        )
        queries.insert_reference(
            conn, from_symbol_id=caller_id, to_symbol_id=populated["child_sym_id"],
            ref_kind="call", file_id=populated["file_id"], line=205,
        )

        refs = queries.get_references_to(conn, populated["child_sym_id"])
        assert len(refs) == 1
        assert refs[0]["from_name"] == "Tick"
        assert refs[0]["line"] == 205

    def test_insert_and_query_references_from(self, populated):
        conn = populated["conn"]
        target_id = queries.insert_symbol(
            conn, name="SetActorLocation", qualified_name="AActor::SetActorLocation",
            kind="function", file_id=populated["file_id"],
            line_start=60, line_end=65, parent_symbol_id=populated["parent_sym_id"],
            access="public", signature="void SetActorLocation(FVector)",
            docstring="", is_ue_macro=0,
        )
        queries.insert_reference(
            conn, from_symbol_id=populated["child_sym_id"], to_symbol_id=target_id,
            ref_kind="call", file_id=populated["file_id"], line=52,
        )

        refs = queries.get_references_from(conn, populated["child_sym_id"])
        assert len(refs) == 1
        assert refs[0]["to_name"] == "SetActorLocation"

    def test_references_filter_by_kind(self, populated):
        conn = populated["conn"]
        caller_id = queries.insert_symbol(
            conn, name="Foo", qualified_name="Foo", kind="function",
            file_id=populated["file_id"], line_start=300, line_end=310,
            parent_symbol_id=None, access="public",
            signature="void Foo()", docstring="", is_ue_macro=0,
        )
        queries.insert_reference(
            conn, from_symbol_id=caller_id, to_symbol_id=populated["child_sym_id"],
            ref_kind="call", file_id=populated["file_id"], line=305,
        )
        queries.insert_reference(
            conn, from_symbol_id=caller_id, to_symbol_id=populated["parent_sym_id"],
            ref_kind="type_use", file_id=populated["file_id"], line=301,
        )

        call_refs = queries.get_references_to(conn, populated["child_sym_id"], ref_kind="call")
        assert len(call_refs) == 1

        type_refs = queries.get_references_to(conn, populated["parent_sym_id"], ref_kind="type_use")
        assert len(type_refs) == 1


# ─── Source FTS tests ────────────────────────────────────────────────────

class TestSourceFTS:
    def test_source_fts_search(self, populated):
        conn = populated["conn"]
        # Insert some source lines
        conn.execute(
            "INSERT INTO source_fts (file_id, line_number, text) VALUES (?, ?, ?)",
            (populated["file_id"], 50, "FVector GetActorLocation() const"),
        )
        conn.execute(
            "INSERT INTO source_fts (file_id, line_number, text) VALUES (?, ?, ?)",
            (populated["file_id"], 51, "{ return RootComponent->GetComponentLocation(); }"),
        )
        conn.commit()

        results = queries.search_source_fts(conn, "GetActorLocation")
        assert len(results) >= 1
        assert results[0]["line_number"] == 50

    def test_source_fts_scope_filter(self, populated):
        conn = populated["conn"]
        # Add a .cpp file
        cpp_file = queries.insert_file(
            conn, path="/Engine/Source/Runtime/Core/Private/Actor.cpp",
            module_id=populated["module_id"], file_type="source",
            line_count=1000, last_modified=1000.0,
        )
        conn.execute(
            "INSERT INTO source_fts (file_id, line_number, text) VALUES (?, ?, ?)",
            (cpp_file, 10, "void AActor::BeginPlay()"),
        )
        conn.execute(
            "INSERT INTO source_fts (file_id, line_number, text) VALUES (?, ?, ?)",
            (populated["file_id"], 15, "virtual void BeginPlay();"),
        )
        conn.commit()

        # Search only headers
        results = queries.search_source_fts(conn, "BeginPlay", scope="header")
        assert len(results) == 1
        assert results[0]["file_id"] == populated["file_id"]


# ─── Module + file query tests ──────────────────────────────────────────

class TestModuleFileQueries:
    def test_get_file_by_path(self, populated):
        f = queries.get_file_by_path(
            populated["conn"], "/Engine/Source/Runtime/Core/Public/Actor.h"
        )
        assert f is not None
        assert f["line_count"] == 500

    def test_get_module_by_name(self, populated):
        m = queries.get_module_by_name(populated["conn"], "CoreModule")
        assert m is not None
        assert m["module_type"] == "Runtime"

    def test_get_symbols_in_module(self, populated):
        syms = queries.get_symbols_in_module(populated["conn"], "CoreModule")
        assert len(syms) >= 2  # AActor + GetActorLocation

    def test_get_module_stats(self, populated):
        stats = queries.get_module_stats(populated["conn"], "CoreModule")
        assert stats is not None
        assert stats["file_count"] == 1
        assert stats["symbol_counts"]["class"] == 1
        assert stats["symbol_counts"]["function"] == 1
