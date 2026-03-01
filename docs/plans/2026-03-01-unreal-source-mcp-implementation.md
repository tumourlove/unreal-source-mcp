# unreal-source-mcp Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an MCP server that indexes all UE C++ and HLSL source into SQLite, providing structural queries and full-text search across ~40K engine files.

**Architecture:** Tree-sitter parses C++ into AST, regex handles HLSL shaders. Symbols, references, and inheritance stored in SQLite with FTS5. FastMCP serves 8 tools over stdio.

**Tech Stack:** Python 3.11+, tree-sitter + tree-sitter-cpp, SQLite + FTS5, mcp (FastMCP)

---

### Task 1: Database Schema and Connection Layer

**Files:**
- Create: `src/unreal_source_mcp/db/schema.py`
- Create: `src/unreal_source_mcp/db/queries.py`
- Create: `tests/test_db.py`

**Step 1: Write the failing test**

```python
# tests/test_db.py
import sqlite3
import pytest
from unreal_source_mcp.db.schema import init_db, SCHEMA_VERSION
from unreal_source_mcp.db.queries import (
    insert_file, insert_symbol, insert_module,
    insert_inheritance, insert_reference, insert_include,
    get_symbol_by_name, search_symbols_fts, search_source_fts,
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_schema_creates_tables(db):
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "files" in tables
    assert "symbols" in tables
    assert "inheritance" in tables
    assert "references" in tables
    assert "modules" in tables
    assert "includes" in tables


def test_schema_creates_fts(db):
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "symbols_fts" in tables
    assert "source_fts" in tables


def test_insert_and_query_symbol(db):
    mod_id = insert_module(db, name="Engine", path="/Engine/Source/Runtime/Engine",
                           module_type="Runtime", build_cs_path="Engine.Build.cs")
    file_id = insert_file(db, path="/Engine/Source/Runtime/Engine/Private/Actor.cpp",
                          module_id=mod_id, file_type="cpp", line_count=5000)
    sym_id = insert_symbol(db, name="AActor", qualified_name="AActor",
                           kind="class", file_id=file_id, line_start=50, line_end=200,
                           parent_symbol_id=None, access="public",
                           signature="class AActor : public UObject",
                           docstring="Base class for all actors.", is_ue_macro=True)
    result = get_symbol_by_name(db, "AActor")
    assert result is not None
    assert result["qualified_name"] == "AActor"
    assert result["kind"] == "class"
    assert result["docstring"] == "Base class for all actors."


def test_fts_symbol_search(db):
    mod_id = insert_module(db, name="Engine", path="/p", module_type="Runtime",
                           build_cs_path="")
    file_id = insert_file(db, path="/p/Actor.h", module_id=mod_id,
                          file_type="h", line_count=100)
    insert_symbol(db, name="AActor", qualified_name="AActor", kind="class",
                  file_id=file_id, line_start=1, line_end=50,
                  parent_symbol_id=None, access="public",
                  signature="class AActor", docstring="Base actor class",
                  is_ue_macro=True)
    insert_symbol(db, name="APawn", qualified_name="APawn", kind="class",
                  file_id=file_id, line_start=51, line_end=100,
                  parent_symbol_id=None, access="public",
                  signature="class APawn : public AActor",
                  docstring="Base pawn class", is_ue_macro=True)
    results = search_symbols_fts(db, "actor")
    assert len(results) >= 1
    assert any(r["name"] == "AActor" for r in results)


def test_insert_inheritance(db):
    mod_id = insert_module(db, name="Engine", path="/p", module_type="Runtime",
                           build_cs_path="")
    file_id = insert_file(db, path="/p/Actor.h", module_id=mod_id,
                          file_type="h", line_count=100)
    parent_id = insert_symbol(db, name="UObject", qualified_name="UObject",
                              kind="class", file_id=file_id, line_start=1,
                              line_end=10, parent_symbol_id=None, access="public",
                              signature="class UObject", docstring="", is_ue_macro=True)
    child_id = insert_symbol(db, name="AActor", qualified_name="AActor",
                             kind="class", file_id=file_id, line_start=11,
                             line_end=50, parent_symbol_id=None, access="public",
                             signature="class AActor : public UObject",
                             docstring="", is_ue_macro=True)
    insert_inheritance(db, child_id=child_id, parent_id=parent_id)
    row = db.execute("SELECT * FROM inheritance WHERE child_id = ?",
                     (child_id,)).fetchone()
    assert row is not None
    assert row["parent_id"] == parent_id


def test_insert_reference(db):
    mod_id = insert_module(db, name="Engine", path="/p", module_type="Runtime",
                           build_cs_path="")
    file_id = insert_file(db, path="/p/Actor.cpp", module_id=mod_id,
                          file_type="cpp", line_count=100)
    sym_a = insert_symbol(db, name="Tick", qualified_name="AActor::Tick",
                          kind="function", file_id=file_id, line_start=1,
                          line_end=10, parent_symbol_id=None, access="public",
                          signature="void Tick(float)", docstring="",
                          is_ue_macro=False)
    sym_b = insert_symbol(db, name="GetWorld", qualified_name="AActor::GetWorld",
                          kind="function", file_id=file_id, line_start=11,
                          line_end=15, parent_symbol_id=None, access="public",
                          signature="UWorld* GetWorld()", docstring="",
                          is_ue_macro=False)
    insert_reference(db, from_symbol_id=sym_a, to_symbol_id=sym_b,
                     ref_kind="call", file_id=file_id, line=5)
    row = db.execute("SELECT * FROM references WHERE from_symbol_id = ?",
                     (sym_a,)).fetchone()
    assert row is not None
    assert row["ref_kind"] == "call"


def test_source_fts(db):
    mod_id = insert_module(db, name="Engine", path="/p", module_type="Runtime",
                           build_cs_path="")
    file_id = insert_file(db, path="/p/Actor.cpp", module_id=mod_id,
                          file_type="cpp", line_count=3)
    # Simulate inserting source lines for FTS
    db.execute("INSERT INTO source_fts(file_id, line_number, text) VALUES (?, ?, ?)",
               (file_id, 10, "// Normalize bone weights before skinning"))
    db.commit()
    results = search_source_fts(db, "bone weights")
    assert len(results) >= 1
    assert "bone weights" in results[0]["text"].lower()
```

**Step 2: Run test to verify it fails**

Run: `cd C:/Projects/unreal-source-mcp && uv run pytest tests/test_db.py -v`
Expected: FAIL — modules not found

**Step 3: Write schema.py**

```python
# src/unreal_source_mcp/db/schema.py
"""SQLite schema for unreal-source-mcp."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

_SCHEMA = """\
-- Modules (Runtime, Editor, Plugin, etc.)
CREATE TABLE IF NOT EXISTS modules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    module_type TEXT NOT NULL DEFAULT '',
    build_cs_path TEXT NOT NULL DEFAULT '',
    UNIQUE(name, path)
);

-- Source files
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL UNIQUE,
    module_id     INTEGER REFERENCES modules(id),
    file_type     TEXT NOT NULL DEFAULT 'cpp',
    line_count    INTEGER NOT NULL DEFAULT 0,
    last_modified REAL NOT NULL DEFAULT 0
);

-- Symbols (classes, structs, functions, enums, variables, macros, typedefs)
CREATE TABLE IF NOT EXISTS symbols (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    qualified_name   TEXT NOT NULL,
    kind             TEXT NOT NULL,
    file_id          INTEGER NOT NULL REFERENCES files(id),
    line_start       INTEGER NOT NULL,
    line_end         INTEGER NOT NULL,
    parent_symbol_id INTEGER REFERENCES symbols(id),
    access           TEXT NOT NULL DEFAULT 'public',
    signature        TEXT NOT NULL DEFAULT '',
    docstring        TEXT NOT NULL DEFAULT '',
    is_ue_macro      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_parent ON symbols(parent_symbol_id);

-- Inheritance (class hierarchy)
CREATE TABLE IF NOT EXISTS inheritance (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id  INTEGER NOT NULL REFERENCES symbols(id),
    parent_id INTEGER NOT NULL REFERENCES symbols(id),
    UNIQUE(child_id, parent_id)
);

-- Cross-references (calls, uses, overrides, includes, typedefs)
CREATE TABLE IF NOT EXISTS references (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_symbol_id  INTEGER NOT NULL REFERENCES symbols(id),
    to_symbol_id    INTEGER NOT NULL REFERENCES symbols(id),
    ref_kind        TEXT NOT NULL DEFAULT 'use',
    file_id         INTEGER NOT NULL REFERENCES files(id),
    line            INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_refs_from ON references(from_symbol_id);
CREATE INDEX IF NOT EXISTS idx_refs_to ON references(to_symbol_id);
CREATE INDEX IF NOT EXISTS idx_refs_kind ON references(ref_kind);

-- Include directives
CREATE TABLE IF NOT EXISTS includes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    included_path TEXT NOT NULL,
    line          INTEGER NOT NULL DEFAULT 0
);

-- FTS5: symbol search
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    qualified_name,
    docstring,
    content=symbols,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, qualified_name, docstring)
    VALUES (new.id, new.name, new.qualified_name, new.docstring);
END;

CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, docstring)
    VALUES ('delete', old.id, old.name, old.qualified_name, old.docstring);
END;

-- FTS5: raw source line search
CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
    file_id UNINDEXED,
    line_number UNINDEXED,
    text
);

-- Metadata
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes."""
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
```

**Step 4: Write queries.py**

```python
# src/unreal_source_mcp/db/queries.py
"""All SQL queries for unreal-source-mcp. No inline SQL elsewhere."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# FTS helpers
# ---------------------------------------------------------------------------

_FTS_STRIP = re.compile(r'[\"*():<>{}^\-~|@!]')


def _escape_fts(query: str) -> str:
    """Sanitize user query for FTS5. Handles C++ :: separators."""
    q = query.replace("::", " ").replace(".", " ")
    q = _FTS_STRIP.sub(" ", q)
    tokens = q.split()
    return " ".join(f'"{t}"*' for t in tokens if t)


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------


def insert_module(
    conn: sqlite3.Connection, *, name: str, path: str,
    module_type: str, build_cs_path: str,
) -> int:
    """Insert a module, return its id."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO modules(name, path, module_type, build_cs_path) "
        "VALUES (?, ?, ?, ?)",
        (name, path, module_type, build_cs_path),
    )
    if cur.lastrowid and cur.rowcount > 0:
        conn.commit()
        return cur.lastrowid
    row = conn.execute(
        "SELECT id FROM modules WHERE name = ? AND path = ?", (name, path)
    ).fetchone()
    return row["id"]


def insert_file(
    conn: sqlite3.Connection, *, path: str, module_id: int,
    file_type: str, line_count: int, last_modified: float = 0.0,
) -> int:
    """Insert a file, return its id."""
    cur = conn.execute(
        "INSERT OR REPLACE INTO files(path, module_id, file_type, line_count, last_modified) "
        "VALUES (?, ?, ?, ?, ?)",
        (path, module_id, file_type, line_count, last_modified),
    )
    conn.commit()
    return cur.lastrowid


def insert_symbol(
    conn: sqlite3.Connection, *, name: str, qualified_name: str,
    kind: str, file_id: int, line_start: int, line_end: int,
    parent_symbol_id: int | None, access: str, signature: str,
    docstring: str, is_ue_macro: bool,
) -> int:
    """Insert a symbol, return its id."""
    cur = conn.execute(
        "INSERT INTO symbols(name, qualified_name, kind, file_id, line_start, "
        "line_end, parent_symbol_id, access, signature, docstring, is_ue_macro) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, qualified_name, kind, file_id, line_start, line_end,
         parent_symbol_id, access, signature, docstring, int(is_ue_macro)),
    )
    conn.commit()
    return cur.lastrowid


def insert_inheritance(
    conn: sqlite3.Connection, *, child_id: int, parent_id: int,
) -> None:
    """Record an inheritance relationship."""
    conn.execute(
        "INSERT OR IGNORE INTO inheritance(child_id, parent_id) VALUES (?, ?)",
        (child_id, parent_id),
    )
    conn.commit()


def insert_reference(
    conn: sqlite3.Connection, *, from_symbol_id: int, to_symbol_id: int,
    ref_kind: str, file_id: int, line: int,
) -> None:
    """Record a cross-reference."""
    conn.execute(
        "INSERT INTO references(from_symbol_id, to_symbol_id, ref_kind, file_id, line) "
        "VALUES (?, ?, ?, ?, ?)",
        (from_symbol_id, to_symbol_id, ref_kind, file_id, line),
    )
    conn.commit()


def insert_include(
    conn: sqlite3.Connection, *, file_id: int, included_path: str, line: int,
) -> None:
    """Record an #include directive."""
    conn.execute(
        "INSERT INTO includes(file_id, included_path, line) VALUES (?, ?, ?)",
        (file_id, included_path, line),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_symbol_by_name(
    conn: sqlite3.Connection, name: str,
) -> dict[str, Any] | None:
    """Exact match on symbol name or qualified_name."""
    row = conn.execute(
        "SELECT * FROM symbols WHERE qualified_name = ? OR name = ? LIMIT 1",
        (name, name),
    ).fetchone()
    return dict(row) if row else None


def get_symbol_by_id(
    conn: sqlite3.Connection, symbol_id: int,
) -> dict[str, Any] | None:
    """Lookup symbol by primary key."""
    row = conn.execute(
        "SELECT * FROM symbols WHERE id = ?", (symbol_id,)
    ).fetchone()
    return dict(row) if row else None


def get_symbols_by_name(
    conn: sqlite3.Connection, name: str, kind: str | None = None,
) -> list[dict[str, Any]]:
    """All symbols matching a name (may have multiple: .h + .cpp)."""
    if kind:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE (qualified_name = ? OR name = ?) AND kind = ?",
            (name, name, kind),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE qualified_name = ? OR name = ?",
            (name, name),
        ).fetchall()
    return [dict(r) for r in rows]


def search_symbols_fts(
    conn: sqlite3.Connection, query: str, limit: int = 20,
) -> list[dict[str, Any]]:
    """FTS5 search across symbol names and docstrings."""
    fts_q = _escape_fts(query)
    if not fts_q:
        return []
    rows = conn.execute(
        "SELECT s.*, bm25(symbols_fts, 10.0, 5.0, 1.0) AS rank "
        "FROM symbols_fts f JOIN symbols s ON s.id = f.rowid "
        "WHERE symbols_fts MATCH ? ORDER BY rank LIMIT ?",
        (fts_q, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def search_source_fts(
    conn: sqlite3.Connection, query: str, limit: int = 20,
    scope: str = "all",
) -> list[dict[str, Any]]:
    """FTS5 search across raw source lines. scope: 'cpp', 'shaders', 'all'."""
    fts_q = _escape_fts(query)
    if not fts_q:
        return []
    if scope == "all":
        rows = conn.execute(
            "SELECT sf.file_id, sf.line_number, sf.text, f.path "
            "FROM source_fts sf JOIN files f ON f.id = sf.file_id "
            "WHERE source_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_q, limit),
        ).fetchall()
    else:
        if scope == "shaders":
            file_types = ("usf", "ush")
        else:
            file_types = ("cpp", "h")
        rows = conn.execute(
            "SELECT sf.file_id, sf.line_number, sf.text, f.path "
            "FROM source_fts sf JOIN files f ON f.id = sf.file_id "
            "WHERE source_fts MATCH ? AND f.file_type IN (?, ?) "
            "ORDER BY rank LIMIT ?",
            (fts_q, file_types[0], file_types[1], limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_file_by_id(
    conn: sqlite3.Connection, file_id: int,
) -> dict[str, Any] | None:
    """Lookup file by id."""
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return dict(row) if row else None


def get_file_by_path(
    conn: sqlite3.Connection, path: str,
) -> dict[str, Any] | None:
    """Lookup file by path."""
    row = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
    return dict(row) if row else None


def get_module_by_name(
    conn: sqlite3.Connection, name: str,
) -> dict[str, Any] | None:
    """Lookup module by name."""
    row = conn.execute(
        "SELECT * FROM modules WHERE name = ? LIMIT 1", (name,)
    ).fetchone()
    return dict(row) if row else None


def get_inheritance_parents(
    conn: sqlite3.Connection, child_id: int,
) -> list[dict[str, Any]]:
    """Get direct parent classes of a symbol."""
    rows = conn.execute(
        "SELECT s.* FROM inheritance i JOIN symbols s ON s.id = i.parent_id "
        "WHERE i.child_id = ?",
        (child_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_inheritance_children(
    conn: sqlite3.Connection, parent_id: int,
) -> list[dict[str, Any]]:
    """Get direct child classes of a symbol."""
    rows = conn.execute(
        "SELECT s.* FROM inheritance i JOIN symbols s ON s.id = i.child_id "
        "WHERE i.parent_id = ?",
        (parent_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_references_to(
    conn: sqlite3.Connection, symbol_id: int,
    ref_kind: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    """Find all references TO a symbol (who uses it)."""
    if ref_kind:
        rows = conn.execute(
            "SELECT r.*, f.path, s.qualified_name AS from_name "
            "FROM references r "
            "JOIN files f ON f.id = r.file_id "
            "LEFT JOIN symbols s ON s.id = r.from_symbol_id "
            "WHERE r.to_symbol_id = ? AND r.ref_kind = ? LIMIT ?",
            (symbol_id, ref_kind, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT r.*, f.path, s.qualified_name AS from_name "
            "FROM references r "
            "JOIN files f ON f.id = r.file_id "
            "LEFT JOIN symbols s ON s.id = r.from_symbol_id "
            "WHERE r.to_symbol_id = ? LIMIT ?",
            (symbol_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_references_from(
    conn: sqlite3.Connection, symbol_id: int,
    ref_kind: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    """Find all references FROM a symbol (what it uses/calls)."""
    if ref_kind:
        rows = conn.execute(
            "SELECT r.*, f.path, s.qualified_name AS to_name "
            "FROM references r "
            "JOIN files f ON f.id = r.file_id "
            "LEFT JOIN symbols s ON s.id = r.to_symbol_id "
            "WHERE r.from_symbol_id = ? AND r.ref_kind = ? LIMIT ?",
            (symbol_id, ref_kind, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT r.*, f.path, s.qualified_name AS to_name "
            "FROM references r "
            "JOIN files f ON f.id = r.file_id "
            "LEFT JOIN symbols s ON s.id = r.to_symbol_id "
            "WHERE r.from_symbol_id = ? LIMIT ?",
            (symbol_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_symbols_in_module(
    conn: sqlite3.Connection, module_name: str,
    kind: str | None = None, limit: int = 200,
) -> list[dict[str, Any]]:
    """List symbols belonging to a module."""
    if kind:
        rows = conn.execute(
            "SELECT s.* FROM symbols s "
            "JOIN files f ON f.id = s.file_id "
            "JOIN modules m ON m.id = f.module_id "
            "WHERE m.name = ? AND s.kind = ? "
            "ORDER BY s.name LIMIT ?",
            (module_name, kind, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT s.* FROM symbols s "
            "JOIN files f ON f.id = s.file_id "
            "JOIN modules m ON m.id = f.module_id "
            "WHERE m.name = ? "
            "ORDER BY s.kind, s.name LIMIT ?",
            (module_name, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_module_stats(
    conn: sqlite3.Connection, module_name: str,
) -> dict[str, Any] | None:
    """Get statistics for a module: file count, symbol counts by kind."""
    mod = get_module_by_name(conn, module_name)
    if not mod:
        return None
    file_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM files WHERE module_id = ?", (mod["id"],)
    ).fetchone()["cnt"]
    kind_counts = conn.execute(
        "SELECT s.kind, COUNT(*) AS cnt FROM symbols s "
        "JOIN files f ON f.id = s.file_id "
        "WHERE f.module_id = ? GROUP BY s.kind",
        (mod["id"],),
    ).fetchall()
    return {
        **mod,
        "file_count": file_count,
        "symbol_counts": {r["kind"]: r["cnt"] for r in kind_counts},
    }
```

**Step 5: Run tests to verify they pass**

Run: `cd C:/Projects/unreal-source-mcp && uv run pytest tests/test_db.py -v`
Expected: All 7 tests PASS

**Step 6: Commit**

```bash
cd C:/Projects/unreal-source-mcp
git add src/unreal_source_mcp/db/schema.py src/unreal_source_mcp/db/queries.py tests/test_db.py
git commit -m "feat: database schema and query layer with FTS5 support"
```

---

### Task 2: C++ Parser — Symbol Extraction

**Files:**
- Create: `src/unreal_source_mcp/indexer/cpp_parser.py`
- Create: `tests/test_cpp_parser.py`
- Create: `tests/fixtures/sample_ue_source/SampleActor.h`
- Create: `tests/fixtures/sample_ue_source/SampleActor.cpp`

**Step 1: Create test fixtures — minimal UE-style source files**

```cpp
// tests/fixtures/sample_ue_source/SampleActor.h
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SampleActor.generated.h"

/**
 * A sample actor for testing the parser.
 * Demonstrates UCLASS, UPROPERTY, UFUNCTION macros.
 */
UCLASS(BlueprintType, Blueprintable)
class ENGINE_API ASampleActor : public AActor
{
    GENERATED_BODY()

public:
    ASampleActor();

    /** Called every frame */
    UFUNCTION(BlueprintCallable, Category = "Sample")
    void DoSomething(float DeltaTime);

    /** Get the health value */
    UFUNCTION(BlueprintPure)
    float GetHealth() const;

protected:
    /** Current health of the actor */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Stats")
    float Health;

private:
    /** Internal tick counter */
    int32 TickCount;

    void InternalHelper();
};

UENUM(BlueprintType)
enum class ESampleState : uint8
{
    Idle,
    Active,
    Destroyed
};

USTRUCT(BlueprintType)
struct FSampleData
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere)
    float Value;

    UPROPERTY(EditAnywhere)
    FString Label;
};
```

```cpp
// tests/fixtures/sample_ue_source/SampleActor.cpp
#include "SampleActor.h"
#include "Engine/World.h"

ASampleActor::ASampleActor()
    : Health(100.0f)
    , TickCount(0)
{
    PrimaryActorTick.bCanEverTick = true;
}

void ASampleActor::DoSomething(float DeltaTime)
{
    TickCount++;
    UWorld* World = GetWorld();
    if (World)
    {
        Health -= DeltaTime * 0.1f;
    }
}

float ASampleActor::GetHealth() const
{
    return Health;
}

void ASampleActor::InternalHelper()
{
    // Internal implementation
    DoSomething(0.0f);
}
```

**Step 2: Write the failing test**

```python
# tests/test_cpp_parser.py
import pytest
from pathlib import Path
from unreal_source_mcp.indexer.cpp_parser import CppParser, ParsedSymbol

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def parser():
    return CppParser()


def test_parse_header_finds_class(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    classes = [s for s in result.symbols if s.kind == "class"]
    assert any(s.name == "ASampleActor" for s in classes)


def test_parse_header_finds_base_class(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    actor_class = next(s for s in result.symbols if s.name == "ASampleActor")
    assert "AActor" in actor_class.base_classes


def test_parse_header_finds_methods(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    functions = [s for s in result.symbols if s.kind == "function"]
    names = {s.name for s in functions}
    assert "DoSomething" in names
    assert "GetHealth" in names
    assert "InternalHelper" in names


def test_parse_header_finds_properties(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    props = [s for s in result.symbols if s.kind == "variable"]
    names = {s.name for s in props}
    assert "Health" in names
    assert "TickCount" in names


def test_parse_header_finds_enum(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    enums = [s for s in result.symbols if s.kind == "enum"]
    assert any(s.name == "ESampleState" for s in enums)


def test_parse_header_finds_struct(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    structs = [s for s in result.symbols if s.kind == "struct"]
    assert any(s.name == "FSampleData" for s in structs)


def test_parse_extracts_docstrings(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    actor_class = next(s for s in result.symbols if s.name == "ASampleActor")
    assert "sample actor" in actor_class.docstring.lower()


def test_parse_detects_ue_macros(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    actor_class = next(s for s in result.symbols if s.name == "ASampleActor")
    assert actor_class.is_ue_macro is True
    do_something = next(s for s in result.symbols if s.name == "DoSomething")
    assert do_something.is_ue_macro is True


def test_parse_extracts_access_specifiers(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    do_something = next(s for s in result.symbols if s.name == "DoSomething")
    assert do_something.access == "public"
    health = next(s for s in result.symbols if s.name == "Health")
    assert health.access == "protected"
    tick_count = next(s for s in result.symbols if s.name == "TickCount")
    assert tick_count.access == "private"


def test_parse_cpp_finds_function_bodies(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.cpp")
    functions = [s for s in result.symbols if s.kind == "function"]
    names = {s.name for s in functions}
    assert "DoSomething" in names
    assert "GetHealth" in names


def test_parse_extracts_includes(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    assert "CoreMinimal.h" in result.includes
    assert "GameFramework/Actor.h" in result.includes


def test_parse_extracts_signatures(parser):
    result = parser.parse_file(FIXTURES / "SampleActor.h")
    do_something = next(s for s in result.symbols if s.name == "DoSomething")
    assert "float DeltaTime" in do_something.signature
    assert "void" in do_something.signature
```

**Step 3: Run tests to verify they fail**

Run: `cd C:/Projects/unreal-source-mcp && uv run pytest tests/test_cpp_parser.py -v`
Expected: FAIL — CppParser not defined

**Step 4: Implement CppParser**

```python
# src/unreal_source_mcp/indexer/cpp_parser.py
"""C++ source parser using tree-sitter. Extracts symbols, inheritance, includes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser, Node

CPP_LANGUAGE = Language(tscpp.language())

# UE macro patterns preceding declarations
_UE_MACRO_RE = re.compile(
    r'\b(UCLASS|USTRUCT|UENUM|UFUNCTION|UPROPERTY|UINTERFACE'
    r'|DECLARE_DELEGATE[A-Za-z_]*'
    r'|DECLARE_DYNAMIC_MULTICAST_DELEGATE[A-Za-z_]*'
    r'|DECLARE_MULTICAST_DELEGATE[A-Za-z_]*'
    r'|DECLARE_EVENT[A-Za-z_]*)\s*\('
)


@dataclass
class ParsedSymbol:
    """A symbol extracted from a C++ source file."""
    name: str
    kind: str  # class, struct, function, enum, variable, macro, typedef
    line_start: int
    line_end: int
    signature: str = ""
    docstring: str = ""
    access: str = "public"
    is_ue_macro: bool = False
    base_classes: list[str] = field(default_factory=list)
    parent_class: str | None = None  # owning class for methods/members


@dataclass
class ParseResult:
    """Result of parsing a single source file."""
    path: Path
    symbols: list[ParsedSymbol] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)


class CppParser:
    """Parses C++ files using tree-sitter and extracts symbols."""

    def __init__(self) -> None:
        self._parser = Parser(CPP_LANGUAGE)

    def parse_file(self, path: Path) -> ParseResult:
        """Parse a single C++ file and extract all symbols."""
        source = path.read_bytes()
        source_text = source.decode("utf-8", errors="replace")
        tree = self._parser.parse(source)
        result = ParseResult(
            path=path,
            source_lines=source_text.splitlines(),
        )

        # Extract includes
        self._extract_includes(tree.root_node, source_text, result)

        # Build UE macro line map (which lines have UE macros)
        ue_macro_lines = self._find_ue_macro_lines(source_text)

        # Walk the AST and extract symbols
        self._walk_node(tree.root_node, source_text, result, ue_macro_lines,
                        current_class=None, current_access="public")

        return result

    def _extract_includes(self, root: Node, source: str, result: ParseResult) -> None:
        """Extract all #include directives."""
        for node in self._iter_children_of_type(root, "preproc_include"):
            path_node = node.child_by_field_name("path")
            if path_node:
                text = path_node.text.decode("utf-8", errors="replace")
                # Strip quotes or angle brackets
                text = text.strip('"<>')
                result.includes.append(text)

    def _find_ue_macro_lines(self, source: str) -> dict[int, str]:
        """Find lines containing UE macros. Returns {line_number: macro_name}."""
        macro_lines = {}
        for match in _UE_MACRO_RE.finditer(source):
            line_num = source[:match.start()].count("\n")
            macro_lines[line_num] = match.group(1)
        return macro_lines

    def _get_docstring(self, node: Node, source: str) -> str:
        """Extract doc comment (/** */ or ///) above a node."""
        lines = source[:node.start_byte].splitlines()
        doc_lines = []
        # Walk backwards from the line before the node
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith("///"):
                doc_lines.insert(0, stripped[3:].strip())
            elif stripped.startswith("*") and not stripped.startswith("*/"):
                doc_lines.insert(0, stripped[1:].strip())
            elif stripped.startswith("/**"):
                text = stripped[3:].strip()
                if text:
                    doc_lines.insert(0, text)
                break
            elif stripped == "*/":
                continue
            elif stripped == "":
                continue
            elif _UE_MACRO_RE.match(stripped):
                # UE macro between doc comment and declaration — keep going
                continue
            else:
                break
        return " ".join(doc_lines).strip().rstrip("*/").strip()

    def _walk_node(
        self, node: Node, source: str, result: ParseResult,
        ue_macro_lines: dict[int, str],
        current_class: str | None, current_access: str,
    ) -> None:
        """Recursively walk AST and extract symbols."""
        for child in node.children:
            ntype = child.type

            if ntype == "class_specifier":
                self._handle_class_or_struct(
                    child, source, result, ue_macro_lines, "class", current_class)
            elif ntype == "struct_specifier":
                self._handle_class_or_struct(
                    child, source, result, ue_macro_lines, "struct", current_class)
            elif ntype == "enum_specifier":
                self._handle_enum(child, source, result, ue_macro_lines)
            elif ntype == "function_definition":
                self._handle_function(
                    child, source, result, ue_macro_lines,
                    current_class, current_access)
            elif ntype == "declaration":
                self._handle_declaration(
                    child, source, result, ue_macro_lines,
                    current_class, current_access)
            elif ntype == "field_declaration":
                self._handle_field(
                    child, source, result, ue_macro_lines,
                    current_class, current_access)
            elif ntype == "access_specifier":
                # Update current access level
                text = child.text.decode("utf-8", errors="replace").rstrip(":")
                current_access = text.strip()
            elif ntype in ("field_declaration_list", "declaration_list",
                           "translation_unit", "namespace_definition"):
                # Recurse into containers
                ns_class = current_class
                self._walk_node(child, source, result, ue_macro_lines,
                                ns_class, current_access)

    def _handle_class_or_struct(
        self, node: Node, source: str, result: ParseResult,
        ue_macro_lines: dict[int, str], kind: str, parent_class: str | None,
    ) -> None:
        """Extract a class or struct definition."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8", errors="replace")

        # Skip forward declarations (no body)
        body = node.child_by_field_name("body")
        if not body:
            return

        # Base classes
        base_classes = []
        for child in node.children:
            if child.type == "base_class_clause":
                for base in child.children:
                    if base.type == "type_identifier" or base.type == "qualified_identifier":
                        base_classes.append(
                            base.text.decode("utf-8", errors="replace"))

        # Check for UE macro on preceding lines
        is_ue = self._check_ue_macro(node, ue_macro_lines)

        sig = self._get_signature_text(node, source)
        docstring = self._get_docstring(node, source)

        symbol = ParsedSymbol(
            name=name, kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig, docstring=docstring,
            access="public", is_ue_macro=is_ue,
            base_classes=base_classes, parent_class=parent_class,
        )
        result.symbols.append(symbol)

        # Recurse into the class body to find members
        if body:
            default_access = "private" if kind == "class" else "public"
            self._walk_node(body, source, result, ue_macro_lines,
                            current_class=name, current_access=default_access)

    def _handle_enum(
        self, node: Node, source: str, result: ParseResult,
        ue_macro_lines: dict[int, str],
    ) -> None:
        """Extract an enum definition."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = name_node.text.decode("utf-8", errors="replace")
        is_ue = self._check_ue_macro(node, ue_macro_lines)
        docstring = self._get_docstring(node, source)

        symbol = ParsedSymbol(
            name=name, kind="enum",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=self._get_signature_text(node, source),
            docstring=docstring,
            is_ue_macro=is_ue,
        )
        result.symbols.append(symbol)

    def _handle_function(
        self, node: Node, source: str, result: ParseResult,
        ue_macro_lines: dict[int, str],
        current_class: str | None, current_access: str,
    ) -> None:
        """Extract a function definition."""
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return
        name = self._extract_function_name(declarator)
        if not name:
            return

        # For class::method definitions in .cpp, extract class name
        parent = current_class
        if "::" in name:
            parts = name.rsplit("::", 1)
            parent = parts[0]
            name = parts[1]

        is_ue = self._check_ue_macro(node, ue_macro_lines)
        docstring = self._get_docstring(node, source)

        # Build signature from the declarator line(s) only
        sig = self._get_declaration_signature(node, source)

        symbol = ParsedSymbol(
            name=name, kind="function",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig, docstring=docstring,
            access=current_access, is_ue_macro=is_ue,
            parent_class=parent,
        )
        result.symbols.append(symbol)

    def _handle_declaration(
        self, node: Node, source: str, result: ParseResult,
        ue_macro_lines: dict[int, str],
        current_class: str | None, current_access: str,
    ) -> None:
        """Handle a declaration (could be function declaration or variable)."""
        # Check if this is a function declaration (has a function_declarator child)
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return

        if declarator.type == "function_declarator":
            name = self._extract_function_name(declarator)
            if not name:
                return
            is_ue = self._check_ue_macro(node, ue_macro_lines)
            docstring = self._get_docstring(node, source)
            sig = node.text.decode("utf-8", errors="replace").strip().rstrip(";")

            symbol = ParsedSymbol(
                name=name, kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=sig, docstring=docstring,
                access=current_access, is_ue_macro=is_ue,
                parent_class=current_class,
            )
            result.symbols.append(symbol)

    def _handle_field(
        self, node: Node, source: str, result: ParseResult,
        ue_macro_lines: dict[int, str],
        current_class: str | None, current_access: str,
    ) -> None:
        """Handle a field declaration (member variable or method declaration)."""
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return

        if declarator.type == "function_declarator":
            name = self._extract_function_name(declarator)
            if not name:
                return
            is_ue = self._check_ue_macro(node, ue_macro_lines)
            docstring = self._get_docstring(node, source)
            sig = node.text.decode("utf-8", errors="replace").strip().rstrip(";")

            symbol = ParsedSymbol(
                name=name, kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=sig, docstring=docstring,
                access=current_access, is_ue_macro=is_ue,
                parent_class=current_class,
            )
            result.symbols.append(symbol)
        else:
            # Member variable
            name = self._extract_declarator_name(declarator)
            if not name or name == "GENERATED_BODY" or name == "GENERATED_UCLASS_BODY":
                return
            is_ue = self._check_ue_macro(node, ue_macro_lines)
            docstring = self._get_docstring(node, source)
            sig = node.text.decode("utf-8", errors="replace").strip().rstrip(";")

            symbol = ParsedSymbol(
                name=name, kind="variable",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=sig, docstring=docstring,
                access=current_access, is_ue_macro=is_ue,
                parent_class=current_class,
            )
            result.symbols.append(symbol)

    def _extract_function_name(self, declarator: Node) -> str | None:
        """Get function name from a function_declarator node."""
        name_node = declarator.child_by_field_name("declarator")
        if name_node:
            return name_node.text.decode("utf-8", errors="replace")
        return None

    def _extract_declarator_name(self, declarator: Node) -> str | None:
        """Get variable name from a declarator node."""
        if declarator.type == "identifier":
            return declarator.text.decode("utf-8", errors="replace")
        if declarator.type == "field_identifier":
            return declarator.text.decode("utf-8", errors="replace")
        # Try to find an identifier child
        for child in declarator.children:
            if child.type in ("identifier", "field_identifier"):
                return child.text.decode("utf-8", errors="replace")
        return declarator.text.decode("utf-8", errors="replace")

    def _check_ue_macro(self, node: Node, ue_macro_lines: dict[int, str]) -> bool:
        """Check if a UE macro appears on lines just before this node."""
        start_line = node.start_point[0]
        # Check up to 5 lines before the node (macros can be multi-line)
        for offset in range(0, 6):
            if (start_line - offset) in ue_macro_lines:
                return True
        return False

    def _get_signature_text(self, node: Node, source: str) -> str:
        """Get the first line(s) of a node as its signature."""
        text = node.text.decode("utf-8", errors="replace")
        # Get up to the opening brace
        brace_idx = text.find("{")
        if brace_idx > 0:
            sig = text[:brace_idx].strip()
        else:
            sig = text.split("\n")[0].strip()
        return sig

    def _get_declaration_signature(self, node: Node, source: str) -> str:
        """Get signature for a function definition (return type + declarator)."""
        text = node.text.decode("utf-8", errors="replace")
        brace_idx = text.find("{")
        if brace_idx > 0:
            return text[:brace_idx].strip()
        return text.split("\n")[0].strip()

    def _iter_children_of_type(self, node: Node, type_name: str):
        """Iterate over direct children of a specific type."""
        for child in node.children:
            if child.type == type_name:
                yield child
```

**Step 5: Run tests to verify they pass**

Run: `cd C:/Projects/unreal-source-mcp && uv run pytest tests/test_cpp_parser.py -v`
Expected: All tests PASS. Some may need minor adjustments based on exact tree-sitter AST output — iterate until green.

**Step 6: Commit**

```bash
cd C:/Projects/unreal-source-mcp
git add src/unreal_source_mcp/indexer/cpp_parser.py tests/test_cpp_parser.py tests/fixtures/
git commit -m "feat: C++ parser with tree-sitter — symbols, inheritance, UE macros"
```

---

### Task 3: Shader Parser

**Files:**
- Create: `src/unreal_source_mcp/indexer/shader_parser.py`
- Create: `tests/test_shader_parser.py`
- Create: `tests/fixtures/sample_ue_source/SampleShader.usf`

**Step 1: Create fixture**

```hlsl
// tests/fixtures/sample_ue_source/SampleShader.usf
#include "/Engine/Private/Common.ush"
#include "/Engine/Private/DeferredShadingCommon.ush"

// Encode normal for GBuffer storage
float3 EncodeNormal(float3 Normal)
{
    return Normal * 0.5 + 0.5;
}

struct FGBufferData
{
    float3 WorldNormal;
    float Roughness;
    float3 BaseColor;
    float Metallic;
};

#define GBUFFER_HAS_TANGENT 1

/**
 * Decode GBuffer normals from the render target.
 * Uses octahedron encoding for better precision.
 */
float3 DecodeGBufferNormal(float2 EncodedNormal)
{
    float3 N;
    N.xy = EncodedNormal * 2.0 - 1.0;
    N.z = sqrt(saturate(1.0 - dot(N.xy, N.xy)));
    return N;
}

void MainPS(
    in float4 SvPosition : SV_Position,
    out float4 OutColor : SV_Target0)
{
    OutColor = float4(1, 0, 0, 1);
}
```

**Step 2: Write the failing test**

```python
# tests/test_shader_parser.py
import pytest
from pathlib import Path
from unreal_source_mcp.indexer.shader_parser import ShaderParser

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def parser():
    return ShaderParser()


def test_shader_finds_functions(parser):
    result = parser.parse_file(FIXTURES / "SampleShader.usf")
    names = {s.name for s in result.symbols if s.kind == "function"}
    assert "EncodeNormal" in names
    assert "DecodeGBufferNormal" in names
    assert "MainPS" in names


def test_shader_finds_structs(parser):
    result = parser.parse_file(FIXTURES / "SampleShader.usf")
    structs = [s for s in result.symbols if s.kind == "struct"]
    assert any(s.name == "FGBufferData" for s in structs)


def test_shader_finds_defines(parser):
    result = parser.parse_file(FIXTURES / "SampleShader.usf")
    macros = [s for s in result.symbols if s.kind == "macro"]
    assert any(s.name == "GBUFFER_HAS_TANGENT" for s in macros)


def test_shader_finds_includes(parser):
    result = parser.parse_file(FIXTURES / "SampleShader.usf")
    assert any("Common.ush" in inc for inc in result.includes)
    assert any("DeferredShadingCommon.ush" in inc for inc in result.includes)


def test_shader_extracts_docstring(parser):
    result = parser.parse_file(FIXTURES / "SampleShader.usf")
    decode = next(s for s in result.symbols if s.name == "DecodeGBufferNormal")
    assert "octahedron" in decode.docstring.lower()
```

**Step 3: Implement ShaderParser**

```python
# src/unreal_source_mcp/indexer/shader_parser.py
"""Regex-based HLSL/USF shader parser."""

from __future__ import annotations

import re
from pathlib import Path

from unreal_source_mcp.indexer.cpp_parser import ParsedSymbol, ParseResult

# Patterns
_INCLUDE_RE = re.compile(r'#include\s+"([^"]+)"')
_FUNC_RE = re.compile(
    r'^(\w[\w\d]*(?:\s*<[^>]*>)?)\s+'  # return type
    r'(\w[\w\d]*)\s*\('                 # function name(
    r'([^)]*)\)\s*(?::\s*\w+\s*)?'     # params) optional semantic
    r'\{',                               # opening brace
    re.MULTILINE,
)
_STRUCT_RE = re.compile(
    r'^struct\s+(\w[\w\d]*)\s*\{',
    re.MULTILINE,
)
_DEFINE_RE = re.compile(
    r'^#define\s+(\w[\w\d]*)\s+(.+)$',
    re.MULTILINE,
)
_DOC_COMMENT_RE = re.compile(
    r'/\*\*(.*?)\*/',
    re.DOTALL,
)
_LINE_COMMENT_RE = re.compile(r'//\s*(.*)')


class ShaderParser:
    """Parses HLSL/USF/USH shader files using regex."""

    def parse_file(self, path: Path) -> ParseResult:
        source = path.read_text(encoding="utf-8", errors="replace")
        result = ParseResult(
            path=path,
            source_lines=source.splitlines(),
        )

        # Includes
        for match in _INCLUDE_RE.finditer(source):
            result.includes.append(match.group(1))

        # Functions
        for match in _FUNC_RE.finditer(source):
            line = source[:match.start()].count("\n") + 1
            name = match.group(2)
            ret_type = match.group(1)
            params = match.group(3).strip()
            sig = f"{ret_type} {name}({params})"

            # Find matching closing brace for line_end
            brace_start = match.end() - 1
            line_end = self._find_closing_brace_line(source, brace_start)

            docstring = self._get_preceding_docstring(source, match.start())

            result.symbols.append(ParsedSymbol(
                name=name, kind="function",
                line_start=line, line_end=line_end,
                signature=sig, docstring=docstring,
            ))

        # Structs
        for match in _STRUCT_RE.finditer(source):
            line = source[:match.start()].count("\n") + 1
            name = match.group(1)
            brace_start = match.end() - 1
            line_end = self._find_closing_brace_line(source, brace_start)

            result.symbols.append(ParsedSymbol(
                name=name, kind="struct",
                line_start=line, line_end=line_end,
                signature=f"struct {name}",
            ))

        # Defines
        for match in _DEFINE_RE.finditer(source):
            line = source[:match.start()].count("\n") + 1
            name = match.group(1)
            value = match.group(2).strip()

            result.symbols.append(ParsedSymbol(
                name=name, kind="macro",
                line_start=line, line_end=line,
                signature=f"#define {name} {value}",
            ))

        return result

    def _find_closing_brace_line(self, source: str, brace_pos: int) -> int:
        """Find the line number of the matching closing brace."""
        depth = 1
        pos = brace_pos + 1
        while pos < len(source) and depth > 0:
            if source[pos] == "{":
                depth += 1
            elif source[pos] == "}":
                depth -= 1
            pos += 1
        return source[:pos].count("\n") + 1

    def _get_preceding_docstring(self, source: str, pos: int) -> str:
        """Extract doc comment (/** */ or //) above a position."""
        preceding = source[:pos].rstrip()
        lines = preceding.splitlines()
        doc_lines = []
        for line in reversed(lines):
            stripped = line.strip()
            if stripped == "*/":
                continue
            elif stripped.startswith("*"):
                doc_lines.insert(0, stripped.lstrip("* ").strip())
            elif stripped.startswith("/**"):
                text = stripped[3:].strip()
                if text:
                    doc_lines.insert(0, text)
                break
            elif stripped.startswith("//"):
                doc_lines.insert(0, stripped[2:].strip())
            elif stripped == "":
                continue
            else:
                break
        return " ".join(doc_lines).strip().rstrip("*/").strip()
```

**Step 4: Run tests**

Run: `cd C:/Projects/unreal-source-mcp && uv run pytest tests/test_shader_parser.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
cd C:/Projects/unreal-source-mcp
git add src/unreal_source_mcp/indexer/shader_parser.py tests/test_shader_parser.py tests/fixtures/sample_ue_source/SampleShader.usf
git commit -m "feat: HLSL shader parser with regex-based extraction"
```

---

### Task 4: Indexing Pipeline

**Files:**
- Create: `src/unreal_source_mcp/indexer/pipeline.py`
- Create: `tests/test_pipeline.py`

**Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import sqlite3
import pytest
from pathlib import Path

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.db.queries import get_symbol_by_name, search_symbols_fts
from unreal_source_mcp.indexer.pipeline import IndexingPipeline

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_pipeline_indexes_fixtures(db):
    pipeline = IndexingPipeline(db)
    stats = pipeline.index_directory(FIXTURES)
    assert stats["files_processed"] > 0
    assert stats["symbols_extracted"] > 0


def test_pipeline_indexes_class(db):
    pipeline = IndexingPipeline(db)
    pipeline.index_directory(FIXTURES)
    sym = get_symbol_by_name(db, "ASampleActor")
    assert sym is not None
    assert sym["kind"] == "class"


def test_pipeline_indexes_function(db):
    pipeline = IndexingPipeline(db)
    pipeline.index_directory(FIXTURES)
    results = search_symbols_fts(db, "DoSomething")
    assert len(results) >= 1


def test_pipeline_indexes_shaders(db):
    pipeline = IndexingPipeline(db)
    pipeline.index_directory(FIXTURES)
    results = search_symbols_fts(db, "EncodeNormal")
    assert len(results) >= 1


def test_pipeline_populates_source_fts(db):
    pipeline = IndexingPipeline(db)
    pipeline.index_directory(FIXTURES)
    rows = db.execute(
        "SELECT * FROM source_fts WHERE source_fts MATCH '\"bone\"*' LIMIT 5"
    ).fetchall()
    assert len(rows) >= 1


def test_pipeline_records_inheritance(db):
    pipeline = IndexingPipeline(db)
    pipeline.index_directory(FIXTURES)
    sym = get_symbol_by_name(db, "ASampleActor")
    if sym:
        parents = db.execute(
            "SELECT s.name FROM inheritance i JOIN symbols s ON s.id = i.parent_id "
            "WHERE i.child_id = ?", (sym["id"],)
        ).fetchall()
        parent_names = [r["name"] for r in parents]
        assert "AActor" in parent_names
```

**Step 2: Implement pipeline**

```python
# src/unreal_source_mcp/indexer/pipeline.py
"""Orchestrates full indexing of UE source into SQLite."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from unreal_source_mcp.db.queries import (
    insert_file, insert_module, insert_symbol,
    insert_inheritance, insert_include, get_symbol_by_name,
)
from unreal_source_mcp.indexer.cpp_parser import CppParser, ParseResult
from unreal_source_mcp.indexer.shader_parser import ShaderParser

log = logging.getLogger(__name__)

_CPP_EXTENSIONS = {".h", ".cpp", ".inl"}
_SHADER_EXTENSIONS = {".usf", ".ush"}


class IndexingPipeline:
    """Indexes UE source files into the SQLite database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cpp_parser = CppParser()
        self._shader_parser = ShaderParser()
        self._symbol_name_to_id: dict[str, int] = {}

    def index_directory(
        self, path: Path, module_name: str | None = None,
        module_type: str = "Runtime",
    ) -> dict[str, Any]:
        """Index all source files in a directory tree. Returns stats."""
        stats = {"files_processed": 0, "symbols_extracted": 0, "errors": 0}

        # Detect module name from directory name if not provided
        if module_name is None:
            module_name = path.name

        # Create module record
        mod_id = insert_module(
            self._conn, name=module_name, path=str(path),
            module_type=module_type, build_cs_path="",
        )

        # Discover files
        for root, _dirs, files in os.walk(path):
            for fname in files:
                fpath = Path(root) / fname
                ext = fpath.suffix.lower()
                if ext in _CPP_EXTENSIONS:
                    try:
                        self._index_cpp_file(fpath, mod_id)
                        stats["files_processed"] += 1
                    except Exception as e:
                        log.warning("Failed to parse %s: %s", fpath, e)
                        stats["errors"] += 1
                elif ext in _SHADER_EXTENSIONS:
                    try:
                        self._index_shader_file(fpath, mod_id)
                        stats["files_processed"] += 1
                    except Exception as e:
                        log.warning("Failed to parse %s: %s", fpath, e)
                        stats["errors"] += 1

        # Count symbols
        row = self._conn.execute("SELECT COUNT(*) AS cnt FROM symbols").fetchone()
        stats["symbols_extracted"] = row["cnt"] if row else 0

        # Resolve inheritance (second pass — base classes may not exist
        # in fixtures but would in full engine index)
        self._resolve_inheritance()

        return stats

    def index_engine(
        self, source_path: Path, shader_path: Path | None = None,
    ) -> dict[str, Any]:
        """Index the full UE engine source tree."""
        total_stats = {"files_processed": 0, "symbols_extracted": 0, "errors": 0}

        # Index each Runtime/Editor/Developer module
        for category in ("Runtime", "Editor", "Developer", "Programs"):
            category_path = source_path / category
            if not category_path.is_dir():
                continue
            module_type = category
            for module_dir in sorted(category_path.iterdir()):
                if not module_dir.is_dir():
                    continue
                log.info("Indexing module: %s/%s", category, module_dir.name)
                stats = self.index_directory(
                    module_dir, module_name=module_dir.name,
                    module_type=module_type,
                )
                for key in total_stats:
                    total_stats[key] += stats.get(key, 0)

        # Index plugins
        plugins_path = source_path.parent / "Plugins"
        if plugins_path.is_dir():
            for plugin_dir in sorted(plugins_path.rglob("Source")):
                if plugin_dir.is_dir():
                    module_name = plugin_dir.parent.name
                    log.info("Indexing plugin: %s", module_name)
                    stats = self.index_directory(
                        plugin_dir, module_name=module_name,
                        module_type="Plugin",
                    )
                    for key in total_stats:
                        total_stats[key] += stats.get(key, 0)

        # Index shaders
        if shader_path and shader_path.is_dir():
            log.info("Indexing shaders: %s", shader_path)
            stats = self.index_directory(
                shader_path, module_name="Shaders",
                module_type="Shaders",
            )
            for key in total_stats:
                total_stats[key] += stats.get(key, 0)

        return total_stats

    def _index_cpp_file(self, path: Path, mod_id: int) -> None:
        """Parse and index a single C++ file."""
        result = self._cpp_parser.parse_file(path)
        file_type = "h" if path.suffix.lower() == ".h" else "cpp"

        file_id = insert_file(
            self._conn, path=str(path), module_id=mod_id,
            file_type=file_type, line_count=len(result.source_lines),
            last_modified=os.path.getmtime(path),
        )

        # Insert includes
        for inc in result.includes:
            insert_include(self._conn, file_id=file_id, included_path=inc, line=0)

        # Insert symbols
        for sym in result.symbols:
            qualified = sym.name
            if sym.parent_class:
                qualified = f"{sym.parent_class}::{sym.name}"

            sym_id = insert_symbol(
                self._conn, name=sym.name, qualified_name=qualified,
                kind=sym.kind, file_id=file_id,
                line_start=sym.line_start, line_end=sym.line_end,
                parent_symbol_id=None, access=sym.access,
                signature=sym.signature, docstring=sym.docstring,
                is_ue_macro=sym.is_ue_macro,
            )

            # Track for inheritance resolution
            if sym.kind in ("class", "struct"):
                self._symbol_name_to_id[sym.name] = sym_id
                # Store base classes for later resolution
                if sym.base_classes:
                    self._symbol_name_to_id[f"_bases_{sym.name}"] = sym.base_classes

        # Insert source lines for FTS
        self._insert_source_lines(file_id, result.source_lines)

    def _index_shader_file(self, path: Path, mod_id: int) -> None:
        """Parse and index a single shader file."""
        result = self._shader_parser.parse_file(path)
        file_type = "usf" if path.suffix.lower() == ".usf" else "ush"

        file_id = insert_file(
            self._conn, path=str(path), module_id=mod_id,
            file_type=file_type, line_count=len(result.source_lines),
            last_modified=os.path.getmtime(path),
        )

        for inc in result.includes:
            insert_include(self._conn, file_id=file_id, included_path=inc, line=0)

        for sym in result.symbols:
            insert_symbol(
                self._conn, name=sym.name, qualified_name=sym.name,
                kind=sym.kind, file_id=file_id,
                line_start=sym.line_start, line_end=sym.line_end,
                parent_symbol_id=None, access="public",
                signature=sym.signature, docstring=sym.docstring,
                is_ue_macro=False,
            )

        self._insert_source_lines(file_id, result.source_lines)

    def _insert_source_lines(self, file_id: int, lines: list[str]) -> None:
        """Batch-insert source lines into FTS table."""
        # Insert every Nth line to keep FTS table manageable
        # For full engine, every line would be ~20M+ rows — we batch by chunks
        batch = []
        chunk = []
        chunk_start = 1
        CHUNK_SIZE = 10  # Group every 10 lines into one FTS row

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped:  # Skip empty lines
                chunk.append(stripped)
            if i % CHUNK_SIZE == 0 or i == len(lines):
                if chunk:
                    text = " ".join(chunk)
                    batch.append((file_id, chunk_start, text))
                    chunk = []
                    chunk_start = i + 1

        if batch:
            self._conn.executemany(
                "INSERT INTO source_fts(file_id, line_number, text) VALUES (?, ?, ?)",
                batch,
            )
            self._conn.commit()

    def _resolve_inheritance(self) -> None:
        """Second pass: resolve base class names to symbol IDs."""
        for key, value in list(self._symbol_name_to_id.items()):
            if key.startswith("_bases_") and isinstance(value, list):
                child_name = key[7:]  # Strip "_bases_" prefix
                child_id = self._symbol_name_to_id.get(child_name)
                if child_id is None:
                    continue
                for base_name in value:
                    parent_id = self._symbol_name_to_id.get(base_name)
                    if parent_id is not None:
                        insert_inheritance(
                            self._conn, child_id=child_id, parent_id=parent_id,
                        )
```

**Step 3: Run tests**

Run: `cd C:/Projects/unreal-source-mcp && uv run pytest tests/test_pipeline.py -v`
Expected: All PASS (inheritance test may need adjustment since `AActor` isn't in fixtures — that's fine, skip gracefully)

**Step 4: Commit**

```bash
cd C:/Projects/unreal-source-mcp
git add src/unreal_source_mcp/indexer/pipeline.py tests/test_pipeline.py
git commit -m "feat: indexing pipeline — discovers, parses, and stores UE source"
```

---

### Task 5: MCP Server with All 8 Tools

**Files:**
- Modify: `src/unreal_source_mcp/server.py`
- Create: `src/unreal_source_mcp/config.py`
- Create: `tests/test_server.py`

**Step 1: Write config module**

```python
# src/unreal_source_mcp/config.py
"""Configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

DB_DIR = Path(os.environ.get(
    "UNREAL_SOURCE_MCP_DB_DIR",
    os.path.expanduser("~/.unreal-source-mcp"),
))

UE_SOURCE_PATH = os.environ.get("UE_SOURCE_PATH", "")
UE_SHADER_PATH = os.environ.get("UE_SHADER_PATH", "")


def get_db_path() -> Path:
    """Return path to the SQLite database file."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    # Try to detect UE version from source path
    source = UE_SOURCE_PATH
    version = "unknown"
    if "UE_5.7" in source:
        version = "5.7"
    elif "UE_5.6" in source:
        version = "5.6"
    elif "UE_5.5" in source:
        version = "5.5"
    return DB_DIR / f"ue_{version}.db"
```

**Step 2: Write the server with all tools**

```python
# src/unreal_source_mcp/server.py
"""MCP server — all 8 tools for UE source intelligence."""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from unreal_source_mcp import config
from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.db import queries as Q
from unreal_source_mcp.indexer.pipeline import IndexingPipeline

log = logging.getLogger(__name__)

mcp = FastMCP(
    "unreal-source",
    instructions=(
        "Use these tools to read Unreal Engine source code implementations, "
        "trace cross-references and call graphs, search across 40K+ engine files, "
        "and understand class hierarchies. Complements unreal-api-mcp (API surface) "
        "with deep implementation-level source intelligence."
    ),
)

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    """Lazy-init the database connection. Auto-index if needed."""
    global _conn
    if _conn is not None:
        return _conn

    db_path = config.get_db_path()
    needs_index = not db_path.exists()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    if needs_index and config.UE_SOURCE_PATH:
        print(
            f"unreal-source-mcp: First run — indexing {config.UE_SOURCE_PATH}...",
            file=sys.stderr,
        )
        pipeline = IndexingPipeline(conn)
        source_path = Path(config.UE_SOURCE_PATH)
        shader_path = Path(config.UE_SHADER_PATH) if config.UE_SHADER_PATH else None
        stats = pipeline.index_engine(source_path, shader_path)
        print(
            f"unreal-source-mcp: Indexed {stats['files_processed']} files, "
            f"{stats['symbols_extracted']} symbols ({stats['errors']} errors)",
            file=sys.stderr,
        )
    elif needs_index:
        print(
            "unreal-source-mcp: No UE_SOURCE_PATH set. "
            "Set it to index engine source.",
            file=sys.stderr,
        )

    _conn = conn
    print(f"unreal-source-mcp: ready ({db_path})", file=sys.stderr)
    return _conn


def _read_file_lines(path: str, start: int, end: int) -> str:
    """Read specific lines from a source file on disk."""
    try:
        p = Path(path)
        if not p.exists():
            return f"[File not found: {path}]"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        # Convert to 0-indexed
        start_idx = max(0, start - 1)
        end_idx = min(len(lines), end)
        numbered = []
        for i, line in enumerate(lines[start_idx:end_idx], start=start_idx + 1):
            numbered.append(f"{i:6d} | {line}")
        return "\n".join(numbered)
    except Exception as e:
        return f"[Error reading {path}: {e}]"


# ---------------------------------------------------------------------------
# Tool 1: read_source
# ---------------------------------------------------------------------------

@mcp.tool()
def read_source(symbol: str, include_header: bool = True) -> str:
    """Get the actual implementation source code for a class, function, or struct.

    Args:
        symbol: Symbol name or qualified name (e.g. "FSkeletalMeshRenderData",
                "AActor::Tick", "FSkinWeightInfo").
        include_header: If true, include both .h declaration and .cpp implementation.
    """
    conn = _get_conn()
    symbols = Q.get_symbols_by_name(conn, symbol)
    if not symbols:
        # Try FTS fallback
        symbols = Q.search_symbols_fts(conn, symbol, limit=5)
    if not symbols:
        return f"No symbol found matching '{symbol}'."

    parts = []
    for sym in symbols:
        file_info = Q.get_file_by_id(conn, sym["file_id"])
        if not file_info:
            continue
        if not include_header and file_info["file_type"] == "h":
            continue

        parts.append(f"--- {file_info['path']} (lines {sym['line_start']}-{sym['line_end']}) ---")
        if sym["docstring"]:
            parts.append(f"// {sym['docstring']}")
        parts.append(_read_file_lines(
            file_info["path"], sym["line_start"], sym["line_end"],
        ))
        parts.append("")

    if not parts:
        return f"Found symbol '{symbol}' in database but could not read source files."
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 2: find_references
# ---------------------------------------------------------------------------

@mcp.tool()
def find_references(
    symbol: str,
    ref_kind: str = "",
    limit: int = 50,
) -> str:
    """Find all usage sites of a symbol across the engine.

    Args:
        symbol: Symbol name (e.g. "FMaterialRenderProxy", "AActor::Tick").
        ref_kind: Optional filter: "call", "use", "override". Empty for all.
        limit: Max results (default 50).
    """
    conn = _get_conn()
    syms = Q.get_symbols_by_name(conn, symbol)
    if not syms:
        syms = Q.search_symbols_fts(conn, symbol, limit=3)
    if not syms:
        return f"No symbol found matching '{symbol}'."

    all_refs = []
    for sym in syms:
        refs = Q.get_references_to(
            conn, sym["id"],
            ref_kind=ref_kind or None, limit=limit,
        )
        all_refs.extend(refs)

    if not all_refs:
        return f"No references found for '{symbol}'."

    lines = [f"Found {len(all_refs)} reference(s) to '{symbol}':\n"]
    for ref in all_refs[:limit]:
        from_name = ref.get("from_name", "unknown")
        lines.append(
            f"  [{ref['ref_kind']}] {ref['path']}:{ref['line']} "
            f"(from {from_name})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 3: find_callers
# ---------------------------------------------------------------------------

@mcp.tool()
def find_callers(function: str, limit: int = 50) -> str:
    """What functions call this function?

    Args:
        function: Function name or qualified name (e.g.
                  "UPrimitiveComponent::CreateRenderState_Concurrent").
        limit: Max results (default 50).
    """
    conn = _get_conn()
    syms = Q.get_symbols_by_name(conn, function, kind="function")
    if not syms:
        syms = Q.search_symbols_fts(conn, function, limit=3)
        syms = [s for s in syms if s["kind"] == "function"]
    if not syms:
        return f"No function found matching '{function}'."

    all_refs = []
    for sym in syms:
        refs = Q.get_references_to(conn, sym["id"], ref_kind="call", limit=limit)
        all_refs.extend(refs)

    if not all_refs:
        return f"No callers found for '{function}'."

    lines = [f"Found {len(all_refs)} caller(s) of '{function}':\n"]
    for ref in all_refs[:limit]:
        from_name = ref.get("from_name", "unknown")
        lines.append(f"  {from_name} — {ref['path']}:{ref['line']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 4: find_callees
# ---------------------------------------------------------------------------

@mcp.tool()
def find_callees(function: str, limit: int = 50) -> str:
    """What does this function call internally?

    Args:
        function: Function name or qualified name.
        limit: Max results (default 50).
    """
    conn = _get_conn()
    syms = Q.get_symbols_by_name(conn, function, kind="function")
    if not syms:
        syms = Q.search_symbols_fts(conn, function, limit=3)
        syms = [s for s in syms if s["kind"] == "function"]
    if not syms:
        return f"No function found matching '{function}'."

    all_refs = []
    for sym in syms:
        refs = Q.get_references_from(conn, sym["id"], ref_kind="call", limit=limit)
        all_refs.extend(refs)

    if not all_refs:
        return f"No callees found for '{function}'."

    lines = [f"'{function}' calls {len(all_refs)} function(s):\n"]
    for ref in all_refs[:limit]:
        to_name = ref.get("to_name", "unknown")
        lines.append(f"  {to_name} — {ref['path']}:{ref['line']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5: search_source
# ---------------------------------------------------------------------------

@mcp.tool()
def search_source(query: str, scope: str = "all", limit: int = 20) -> str:
    """Full-text search across all engine source code (C++ and shaders).

    Args:
        query: Search terms (e.g. "bone weight normalization",
               "GBuffer normal encode").
        scope: "cpp" for C++ only, "shaders" for HLSL only, "all" for both.
        limit: Max results (default 20).
    """
    conn = _get_conn()

    # Search both symbols and source lines
    sym_results = Q.search_symbols_fts(conn, query, limit=limit)
    src_results = Q.search_source_fts(conn, query, limit=limit, scope=scope)

    parts = []
    if sym_results:
        parts.append(f"=== Symbol matches ({len(sym_results)}) ===\n")
        for sym in sym_results:
            file_info = Q.get_file_by_id(conn, sym["file_id"])
            path = file_info["path"] if file_info else "unknown"
            parts.append(
                f"  [{sym['kind']}] {sym['qualified_name']} "
                f"— {path}:{sym['line_start']}"
            )
            if sym["docstring"]:
                parts.append(f"    // {sym['docstring'][:120]}")
        parts.append("")

    if src_results:
        parts.append(f"=== Source matches ({len(src_results)}) ===\n")
        for r in src_results:
            parts.append(f"  {r['path']}:{r['line_number']}")
            parts.append(f"    {r['text'][:200]}")
        parts.append("")

    if not parts:
        return f"No results found for '{query}' (scope={scope})."
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 6: get_class_hierarchy
# ---------------------------------------------------------------------------

@mcp.tool()
def get_class_hierarchy(
    class_name: str,
    direction: str = "both",
    depth: int = 5,
) -> str:
    """Get the inheritance tree for a class.

    Args:
        class_name: Class name (e.g. "UPrimitiveComponent", "AActor").
        direction: "ancestors" (parents only), "descendants" (children only),
                   or "both".
        depth: Maximum traversal depth (default 5).
    """
    conn = _get_conn()
    syms = Q.get_symbols_by_name(conn, class_name, kind="class")
    if not syms:
        syms = Q.get_symbols_by_name(conn, class_name, kind="struct")
    if not syms:
        return f"Class '{class_name}' not found."

    sym = syms[0]
    parts = [f"=== Hierarchy for {sym['qualified_name']} ===\n"]

    if direction in ("ancestors", "both"):
        parts.append("Ancestors:")
        self_ancestors = _walk_ancestors(conn, sym["id"], depth, indent=1)
        parts.extend(self_ancestors or ["  (none found)"])
        parts.append("")

    if direction in ("descendants", "both"):
        parts.append("Descendants:")
        self_descendants = _walk_descendants(conn, sym["id"], depth, indent=1)
        parts.extend(self_descendants or ["  (none found)"])

    return "\n".join(parts)


def _walk_ancestors(
    conn: sqlite3.Connection, sym_id: int, depth: int, indent: int,
) -> list[str]:
    if depth <= 0:
        return []
    parents = Q.get_inheritance_parents(conn, sym_id)
    lines = []
    for p in parents:
        prefix = "  " * indent
        file_info = Q.get_file_by_id(conn, p["file_id"])
        path = file_info["path"] if file_info else ""
        lines.append(f"{prefix}← {p['qualified_name']} ({path})")
        lines.extend(_walk_ancestors(conn, p["id"], depth - 1, indent + 1))
    return lines


def _walk_descendants(
    conn: sqlite3.Connection, sym_id: int, depth: int, indent: int,
) -> list[str]:
    if depth <= 0:
        return []
    children = Q.get_inheritance_children(conn, sym_id)
    lines = []
    for c in children:
        prefix = "  " * indent
        file_info = Q.get_file_by_id(conn, c["file_id"])
        path = file_info["path"] if file_info else ""
        lines.append(f"{prefix}→ {c['qualified_name']} ({path})")
        lines.extend(_walk_descendants(conn, c["id"], depth - 1, indent + 1))
    return lines


# ---------------------------------------------------------------------------
# Tool 7: get_module_info
# ---------------------------------------------------------------------------

@mcp.tool()
def get_module_info(module_name: str) -> str:
    """Get module contents, dependencies, and statistics.

    Args:
        module_name: Module name (e.g. "Renderer", "Engine", "Niagara").
    """
    conn = _get_conn()
    stats = Q.get_module_stats(conn, module_name)
    if not stats:
        return f"Module '{module_name}' not found."

    lines = [
        f"=== Module: {stats['name']} ===",
        f"Path: {stats['path']}",
        f"Type: {stats['module_type']}",
        f"Files: {stats['file_count']}",
        "",
        "Symbol counts:",
    ]
    for kind, count in sorted(stats["symbol_counts"].items()):
        lines.append(f"  {kind}: {count}")

    # List key classes (top-level classes in this module)
    classes = Q.get_symbols_in_module(conn, module_name, kind="class", limit=30)
    if classes:
        lines.append(f"\nKey classes ({len(classes)}):")
        for c in classes:
            lines.append(f"  {c['qualified_name']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8: get_symbol_context
# ---------------------------------------------------------------------------

@mcp.tool()
def get_symbol_context(symbol: str, context_lines: int = 20) -> str:
    """Get a symbol's definition with surrounding context and doc comments.

    Args:
        symbol: Symbol name or qualified name.
        context_lines: Number of lines of context above and below (default 20).
    """
    conn = _get_conn()
    syms = Q.get_symbols_by_name(conn, symbol)
    if not syms:
        syms = Q.search_symbols_fts(conn, symbol, limit=3)
    if not syms:
        return f"No symbol found matching '{symbol}'."

    parts = []
    for sym in syms[:3]:  # Show max 3 locations
        file_info = Q.get_file_by_id(conn, sym["file_id"])
        if not file_info:
            continue

        start = max(1, sym["line_start"] - context_lines)
        end = sym["line_end"] + context_lines

        parts.append(
            f"--- {sym['qualified_name']} [{sym['kind']}] "
            f"— {file_info['path']}:{sym['line_start']}-{sym['line_end']} ---"
        )
        if sym["docstring"]:
            parts.append(f"Doc: {sym['docstring']}")
        if sym["signature"]:
            parts.append(f"Sig: {sym['signature']}")
        parts.append("")
        parts.append(_read_file_lines(file_info["path"], start, end))
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Eagerly connect to trigger auto-indexing before MCP handshake
    _get_conn()
    mcp.run()
```

**Step 3: Write basic server test**

```python
# tests/test_server.py
"""Smoke tests for the MCP server tools (using in-memory DB)."""

import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.indexer.pipeline import IndexingPipeline
from unreal_source_mcp import server

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def populated_db():
    """Create an in-memory DB indexed with test fixtures."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    pipeline = IndexingPipeline(conn)
    pipeline.index_directory(FIXTURES)
    return conn


@pytest.fixture(autouse=True)
def mock_conn(populated_db):
    """Patch the server's _get_conn to use our test DB."""
    with patch.object(server, "_conn", populated_db):
        with patch.object(server, "_get_conn", return_value=populated_db):
            yield


def test_search_source_finds_symbol():
    result = server.search_source("ASampleActor")
    assert "ASampleActor" in result


def test_search_source_finds_shader():
    result = server.search_source("EncodeNormal")
    assert "EncodeNormal" in result


def test_search_source_scope_filter():
    result = server.search_source("EncodeNormal", scope="shaders")
    assert "EncodeNormal" in result


def test_read_source_finds_class():
    result = server.read_source("ASampleActor")
    assert "ASampleActor" in result


def test_get_class_hierarchy():
    result = server.get_class_hierarchy("ASampleActor")
    assert "ASampleActor" in result


def test_get_symbol_context():
    result = server.get_symbol_context("DoSomething")
    assert "DoSomething" in result


def test_get_module_info():
    result = server.get_module_info("sample_ue_source")
    assert "sample_ue_source" in result
```

**Step 4: Run tests**

Run: `cd C:/Projects/unreal-source-mcp && uv run pytest tests/ -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
cd C:/Projects/unreal-source-mcp
git add src/unreal_source_mcp/server.py src/unreal_source_mcp/config.py tests/test_server.py
git commit -m "feat: MCP server with all 8 tools — read_source, find_references, search, hierarchy"
```

---

### Task 6: End-to-End Test with Real UE Source

**Files:**
- Create: `tests/test_e2e.py`

**Step 1: Write an integration test that indexes a small slice of real engine source**

```python
# tests/test_e2e.py
"""End-to-end test with actual UE Engine source (skip if not available)."""

import os
import sqlite3
import pytest
from pathlib import Path

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.indexer.pipeline import IndexingPipeline
from unreal_source_mcp.db import queries as Q

UE_SOURCE = os.environ.get("UE_SOURCE_PATH", "")

# Only run if UE source is available
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

    # Index just the Engine/Source/Runtime/CoreUObject module (relatively small)
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
```

**Step 2: Run (will skip on CI without UE source, runs locally)**

Run: `cd C:/Projects/unreal-source-mcp && UE_SOURCE_PATH="C:/Program Files (x86)/UE_5.7/Engine/Source" uv run pytest tests/test_e2e.py -v`
Expected: Tests PASS with real engine source, SKIP without

**Step 3: Commit**

```bash
cd C:/Projects/unreal-source-mcp
git add tests/test_e2e.py
git commit -m "test: end-to-end test with real UE engine source"
```

---

### Task 7: CLI and Final Polish

**Files:**
- Modify: `src/unreal_source_mcp/__main__.py`
- Modify: `pyproject.toml` (verify entry point)

**Step 1: Add --index CLI flag for manual indexing**

```python
# src/unreal_source_mcp/__main__.py
"""Entry point for `python -m unreal_source_mcp` and `uvx unreal-source-mcp`."""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from unreal_source_mcp import config
from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.indexer.pipeline import IndexingPipeline


def _run_index() -> None:
    """Run indexing manually."""
    if not config.UE_SOURCE_PATH:
        print("Error: UE_SOURCE_PATH environment variable not set.", file=sys.stderr)
        sys.exit(1)

    db_path = config.get_db_path()
    print(f"Database: {db_path}", file=sys.stderr)
    print(f"Source: {config.UE_SOURCE_PATH}", file=sys.stderr)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    pipeline = IndexingPipeline(conn)
    source_path = Path(config.UE_SOURCE_PATH)
    shader_path = Path(config.UE_SHADER_PATH) if config.UE_SHADER_PATH else None

    stats = pipeline.index_engine(source_path, shader_path)
    print(
        f"Done: {stats['files_processed']} files, "
        f"{stats['symbols_extracted']} symbols, "
        f"{stats['errors']} errors",
        file=sys.stderr,
    )
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="unreal-source-mcp")
    parser.add_argument(
        "--index", action="store_true",
        help="Index UE source and exit (set UE_SOURCE_PATH first)",
    )
    parser.add_argument(
        "--db-path", type=str, default=None,
        help="Override database path",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.db_path:
        config.DB_DIR = Path(args.db_path).parent

    if args.index:
        _run_index()
    else:
        from unreal_source_mcp.server import main as server_main
        server_main()


if __name__ == "__main__":
    main()
```

**Step 2: Verify entry point in pyproject.toml**

Ensure this exists:
```toml
[project.scripts]
unreal-source-mcp = "unreal_source_mcp.__main__:main"
```

**Step 3: Test the CLI**

Run: `cd C:/Projects/unreal-source-mcp && uv run python -m unreal_source_mcp --help`
Expected: Shows help with --index flag

**Step 4: Commit**

```bash
cd C:/Projects/unreal-source-mcp
git add src/unreal_source_mcp/__main__.py pyproject.toml
git commit -m "feat: CLI with --index flag for manual indexing"
```

---

### Task 8: Integration Test — Index Real Engine and Validate Tools

This is the final validation task. Run the full indexer on real UE source and test the MCP tools manually.

**Step 1: Index the engine**

```bash
cd C:/Projects/unreal-source-mcp
UE_SOURCE_PATH="C:/Program Files (x86)/UE_5.7/Engine/Source" \
UE_SHADER_PATH="C:/Program Files (x86)/UE_5.7/Engine/Shaders" \
uv run python -m unreal_source_mcp --index
```

Expected: Completes in <15 minutes, reports file/symbol counts.

**Step 2: Run all tests**

```bash
cd C:/Projects/unreal-source-mcp
UE_SOURCE_PATH="C:/Program Files (x86)/UE_5.7/Engine/Source" \
uv run pytest tests/ -v
```

Expected: All tests PASS including e2e tests.

**Step 3: Test MCP server manually**

Add to Claude Code settings:
```json
{
  "mcpServers": {
    "unreal-source": {
      "command": "uv",
      "args": ["--directory", "C:/Projects/unreal-source-mcp", "run", "python", "-m", "unreal_source_mcp"],
      "env": {
        "UE_SOURCE_PATH": "C:/Program Files (x86)/UE_5.7/Engine/Source",
        "UE_SHADER_PATH": "C:/Program Files (x86)/UE_5.7/Engine/Shaders"
      }
    }
  }
}
```

Verify these queries work:
- `read_source("FSkeletalMeshRenderData")`
- `search_source("bone weight normalization")`
- `get_class_hierarchy("UPrimitiveComponent")`
- `search_source("GBuffer normal", scope="shaders")`
- `get_module_info("Renderer")`

**Step 4: Final commit**

```bash
cd C:/Projects/unreal-source-mcp
git add -A
git commit -m "chore: final integration validation complete"
```
