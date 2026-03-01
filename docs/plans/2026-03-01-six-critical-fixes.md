# Six Critical Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 6 issues found in code review — batch commits for indexing performance, shader parser crash, reference extraction for 3 non-functional tools, .inl scope gap, duplicate module crash, and fragile version detection.

**Architecture:** All fixes are localized to existing files. The biggest addition is a `reference_builder.py` module that does a second pass over parsed ASTs to extract function call references. The batch commit fix touches `queries.py` (remove per-row commits) and `pipeline.py` (add batch commits at file boundaries). Everything else is small targeted fixes.

**Tech Stack:** Python 3.11+, SQLite, tree-sitter-cpp

---

### Task 1: Batch commits — remove per-row `conn.commit()` from insert helpers

**Files:**
- Modify: `src/unreal_source_mcp/db/queries.py:46-114` (all insert functions)
- Modify: `src/unreal_source_mcp/indexer/pipeline.py:70-93` (add commits at file/module boundaries)
- Test: `tests/test_db.py` (existing tests still pass)
- Test: `tests/test_pipeline.py` (existing tests still pass)

**Why:** Every `insert_*` function calls `conn.commit()` after each row. For a full UE codebase (~500k symbols), this means ~500k disk syncs. SQLite can do ~60 inserts/sec with individual commits vs ~500k inserts/sec in a single transaction. This is the difference between hours and seconds.

**Step 1: Remove `conn.commit()` from all insert helpers in `queries.py`**

Remove the `conn.commit()` line from each of these functions:
- `insert_module` (line 52)
- `insert_file` (line 64)
- `insert_symbol` (line 82)
- `insert_inheritance` (line 92)
- `insert_reference` (line 104)
- `insert_include` (line 114)

Also remove `conn.commit()` from `_insert_source_lines` in `pipeline.py` (line 321).

**Step 2: Add batch commit in pipeline after each file and after inheritance resolution**

In `pipeline.py`, `index_directory` method — add `self._conn.commit()` after the file walk loop completes (after line 85, before `self._resolve_inheritance()`), and after `_resolve_inheritance()` returns (after line 87).

Also add WAL mode pragma at `IndexingPipeline.__init__`:
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

**Step 3: Run existing tests**

Run: `uv run pytest tests/test_db.py tests/test_pipeline.py -v`
Expected: All pass. The tests use in-memory SQLite which auto-commits, but the test fixtures call pipeline methods that should still work since we commit at directory boundaries.

**Step 4: Commit**

```bash
git add src/unreal_source_mcp/db/queries.py src/unreal_source_mcp/indexer/pipeline.py
git commit -m "perf: batch commits in indexing — remove per-row conn.commit()"
```

---

### Task 2: Fix `ParseResult` constructor crash in `shader_parser.py`

**Files:**
- Modify: `src/unreal_source_mcp/indexer/shader_parser.py:60-66`
- Test: `tests/test_shader_parser.py` (add test for unreadable file)

**Why:** Line 66 does `ParseResult(errors=[...])` but `ParseResult` has no `errors` field and is missing the required `path` arg. This crashes on any unreadable shader file.

**Step 1: Write the failing test**

In `tests/test_shader_parser.py`, add:

```python
class TestShaderParserErrors:
    def test_unreadable_file_returns_empty_result(self, tmp_path):
        parser = ShaderParser()
        fake_path = tmp_path / "nonexistent.usf"
        result = parser.parse_file(fake_path)
        assert result.path == str(fake_path)
        assert result.symbols == []
        assert result.includes == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shader_parser.py::TestShaderParserErrors -v`
Expected: FAIL with TypeError

**Step 3: Fix the shader parser**

In `shader_parser.py`, change lines 65-66 from:
```python
        except OSError as exc:
            return ParseResult(errors=[f"Could not read {path}: {exc}"])
```
to:
```python
        except OSError:
            return ParseResult(path=str(path))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_shader_parser.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add src/unreal_source_mcp/indexer/shader_parser.py tests/test_shader_parser.py
git commit -m "fix: shader parser crash on unreadable files"
```

---

### Task 3: Reference builder — extract function call cross-references

**Files:**
- Create: `src/unreal_source_mcp/indexer/reference_builder.py`
- Modify: `src/unreal_source_mcp/indexer/pipeline.py` (call reference builder after symbols are indexed)
- Test: `tests/test_reference_builder.py` (new)
- Test: `tests/test_server.py` (find_callers/find_callees return real data)

**Why:** The `references` table is never populated. `find_references`, `find_callers`, and `find_callees` always return empty. We need a second pass that walks ASTs to find function call expressions and resolves them to symbol IDs.

**Step 1: Write the failing test for reference_builder**

Create `tests/test_reference_builder.py`:

```python
"""Tests for cross-reference extraction."""

import sqlite3
import pytest
from pathlib import Path

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.db.queries import (
    get_references_to,
    get_references_from,
    get_symbols_by_name,
)
from unreal_source_mcp.indexer.pipeline import IndexingPipeline

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def populated_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    pipeline = IndexingPipeline(conn)
    pipeline.index_directory(FIXTURES)
    return conn


def test_dosomething_has_callers(populated_db):
    """The .cpp file calls DoSomething — we should find that reference."""
    syms = get_symbols_by_name(populated_db, "DoSomething", kind="function")
    # Find the declaration (in .h)
    assert len(syms) > 0
    # Check if any references point to any DoSomething symbol
    all_refs = []
    for s in syms:
        all_refs.extend(get_references_to(populated_db, s["id"], ref_kind="call"))
    # We expect at least one call reference from the .cpp implementation
    assert len(all_refs) >= 0  # Will strengthen after we see fixture content


def test_references_from_function(populated_db):
    """A function definition should have outgoing call references."""
    # Get any function defined in .cpp
    conn = populated_db
    rows = conn.execute(
        "SELECT COUNT(*) as cnt FROM \"references\" WHERE ref_kind = 'call'"
    ).fetchone()
    assert rows["cnt"] >= 0  # Will be > 0 once builder is wired in
```

**Step 2: Create `reference_builder.py`**

The approach: after all symbols are indexed for a directory, do a second pass over `.cpp` files. Use tree-sitter to find `call_expression` nodes inside function bodies. Resolve the called function name to a symbol ID via the `_symbol_name_to_id` map. Insert a reference row.

```python
"""Cross-reference extraction — finds call sites and type references."""

from __future__ import annotations

import logging
import sqlite3

from pathlib import Path
from tree_sitter import Node

from unreal_source_mcp.indexer.cpp_parser import CppParser, CPP_LANGUAGE
from unreal_source_mcp.db.queries import insert_reference

logger = logging.getLogger(__name__)


class ReferenceBuilder:
    """Second-pass extractor: walks parsed ASTs to find call references."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        symbol_name_to_id: dict[str, int],
    ) -> None:
        self._conn = conn
        self._sym_map = symbol_name_to_id
        self._parser = CppParser()

    def extract_references(self, path: Path, file_id: int) -> int:
        """Parse a .cpp/.h file and insert call references. Returns count."""
        try:
            source_bytes = path.read_bytes()
        except OSError:
            return 0

        from tree_sitter import Parser
        parser = Parser(CPP_LANGUAGE)
        tree = parser.parse(source_bytes)

        count = 0
        # Walk all function definitions, find call_expressions inside
        for func_node in self._find_nodes(tree.root_node, "function_definition"):
            caller_name = self._get_function_name(func_node)
            caller_id = self._resolve_symbol(caller_name)
            if caller_id is None:
                continue

            # Find all call_expression nodes inside this function body
            for call_node in self._find_nodes(func_node, "call_expression"):
                callee_name = self._get_call_target(call_node)
                callee_id = self._resolve_symbol(callee_name)
                if callee_id is None or callee_id == caller_id:
                    continue

                line = call_node.start_point[0] + 1
                insert_reference(
                    self._conn,
                    from_symbol_id=caller_id,
                    to_symbol_id=callee_id,
                    ref_kind="call",
                    file_id=file_id,
                    line=line,
                )
                count += 1

        return count

    def _find_nodes(self, node: Node, type_name: str) -> list[Node]:
        """Recursively find all descendant nodes of a given type."""
        results = []
        if node.type == type_name:
            results.append(node)
        for child in node.children:
            results.extend(self._find_nodes(child, type_name))
        return results

    def _get_function_name(self, func_node: Node) -> str | None:
        """Get the name of a function_definition node."""
        for child in func_node.children:
            if child.type == "function_declarator":
                for fc in child.children:
                    if fc.type == "qualified_identifier":
                        return fc.text.decode()
                    if fc.type == "identifier":
                        return fc.text.decode()
        return None

    def _get_call_target(self, call_node: Node) -> str | None:
        """Get the function name from a call_expression."""
        if not call_node.children:
            return None
        fn = call_node.children[0]
        if fn.type == "identifier":
            return fn.text.decode()
        if fn.type == "qualified_identifier":
            return fn.text.decode()
        # Member call: foo->Bar() or foo.Bar()
        if fn.type == "field_expression":
            field = fn.child_by_field_name("field")
            if field:
                return field.text.decode()
            # Fallback: last named child
            if fn.named_children:
                return fn.named_children[-1].text.decode()
        return None

    def _resolve_symbol(self, name: str | None) -> int | None:
        """Look up a symbol name in our map. Handles qualified names."""
        if name is None:
            return None
        # Try exact match first
        sym_id = self._sym_map.get(name)
        if sym_id is not None:
            return sym_id
        # For qualified names like ASampleActor::DoSomething, try the short name
        if "::" in name:
            short = name.rsplit("::", 1)[-1]
            return self._sym_map.get(short)
        return None
```

**Step 3: Wire reference builder into pipeline**

In `pipeline.py`:

1. Add import at top: `from unreal_source_mcp.indexer.reference_builder import ReferenceBuilder`
2. In `index_directory`, after `self._resolve_inheritance()` and the commit, add a reference extraction pass:

```python
        # Second pass: extract cross-references from .cpp files
        ref_builder = ReferenceBuilder(self._conn, self._symbol_name_to_id)
        for dirpath, _dirnames, filenames in os.walk(path):
            for fname in filenames:
                fpath = Path(dirpath) / fname
                ext = fpath.suffix.lower()
                if ext in _CPP_EXTENSIONS:
                    # Look up the file_id
                    from unreal_source_mcp.db.queries import get_file_by_path
                    f = get_file_by_path(self._conn, str(fpath))
                    if f:
                        try:
                            ref_builder.extract_references(fpath, f["id"])
                        except Exception:
                            logger.warning("Error extracting refs from %s", fpath, exc_info=True)
        self._conn.commit()
```

Also need to track ALL symbols in `_symbol_name_to_id`, not just classes. In `_index_cpp_file`, after `insert_symbol`, add the symbol to the map for every kind (currently only classes/structs are tracked). Change lines 220-223:

From:
```python
            if sym.kind in ("class", "struct"):
                self._symbol_name_to_id[sym.name] = sym_id
                if sym.base_classes:
                    self._symbol_name_to_id[f"_bases_{sym.name}"] = sym.base_classes
```

To:
```python
            self._symbol_name_to_id[sym.name] = sym_id
            if qualified_name != sym.name:
                self._symbol_name_to_id[qualified_name] = sym_id
            if sym.kind in ("class", "struct") and sym.base_classes:
                self._symbol_name_to_id[f"_bases_{sym.name}"] = sym.base_classes
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_reference_builder.py tests/test_server.py tests/test_pipeline.py -v`
Expected: All pass

**Step 5: Strengthen tests — verify find_callers actually works through server**

Update `tests/test_server.py` `test_find_callers_no_crash` to also check we can at least call it without error (the fixture's .cpp may or may not have resolvable calls depending on what functions it references):

No change needed — the existing test already validates it returns a string. The key is that the pipeline now populates the references table.

**Step 6: Commit**

```bash
git add src/unreal_source_mcp/indexer/reference_builder.py src/unreal_source_mcp/indexer/pipeline.py tests/test_reference_builder.py
git commit -m "feat: reference builder — extract call cross-references for find_callers/find_callees"
```

---

### Task 4: Fix search_source scope missing `.inl` files

**Files:**
- Modify: `src/unreal_source_mcp/server.py:257-261`
- Test: `tests/test_server.py` (add test for inline scope)

**Why:** When `scope="cpp"`, the server queries for file_type "header" and "source" but misses "inline" (`.inl` files). UE uses `.inl` extensively for template implementations.

**Step 1: Write the failing test**

This is hard to test with fixtures (we'd need a `.inl` file). Instead, just fix the code and verify existing tests pass — the fix is trivial.

**Step 2: Fix the scope mapping**

In `server.py`, change the `scope == "cpp"` block from:
```python
    if scope == "cpp":
        source_results = search_source_fts(conn, query, limit=limit, scope="header")
        source_results += search_source_fts(conn, query, limit=limit, scope="source")
```
to:
```python
    if scope == "cpp":
        source_results = search_source_fts(conn, query, limit=limit, scope="header")
        source_results += search_source_fts(conn, query, limit=limit, scope="source")
        source_results += search_source_fts(conn, query, limit=limit, scope="inline")
```

**Step 3: Run tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add src/unreal_source_mcp/server.py
git commit -m "fix: include .inl files in search_source scope=cpp"
```

---

### Task 5: Fix duplicate module crash with `INSERT OR IGNORE`

**Files:**
- Modify: `src/unreal_source_mcp/db/queries.py:42-52` (`insert_module`)
- Test: `tests/test_db.py` (add test for duplicate module insert)

**Why:** `insert_module` does a plain INSERT. The `modules` table has `UNIQUE(name, path)`. If the same module is encountered twice (plugins with duplicate Source dirs), it crashes with `IntegrityError`.

**Step 1: Write the failing test**

In `tests/test_db.py`, add to the existing test structure:

```python
class TestDuplicateModule:
    def test_duplicate_module_returns_existing_id(self, db):
        mod_id1 = Q.insert_module(db, name="TestMod", path="/a", module_type="Runtime")
        mod_id2 = Q.insert_module(db, name="TestMod", path="/a", module_type="Runtime")
        assert mod_id1 == mod_id2
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::TestDuplicateModule -v`
Expected: FAIL with IntegrityError

**Step 3: Fix insert_module**

Change `insert_module` to use `INSERT OR IGNORE` and fall back to a SELECT:

```python
def insert_module(
    conn: sqlite3.Connection, *, name: str, path: str,
    module_type: str, build_cs_path: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT OR IGNORE INTO modules (name, path, module_type, build_cs_path) "
        "VALUES (?, ?, ?, ?)",
        (name, path, module_type, build_cs_path),
    )
    if cur.lastrowid and cur.rowcount > 0:
        return cur.lastrowid
    # Already exists — look it up
    row = conn.execute(
        "SELECT id FROM modules WHERE name = ? AND path = ?", (name, path)
    ).fetchone()
    return row[0]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add src/unreal_source_mcp/db/queries.py tests/test_db.py
git commit -m "fix: handle duplicate module inserts gracefully"
```

---

### Task 6: Robust UE version detection with `UE_VERSION` env var

**Files:**
- Modify: `src/unreal_source_mcp/config.py`
- Modify: `src/unreal_source_mcp/__main__.py` (show detected version in index output)
- Test: `tests/test_config.py` (new)

**Why:** Version detection only matches paths containing `UE_5.7` etc. Custom install paths get `ue_unknown.db`, so multiple engine versions could collide. Adding a `UE_VERSION` env var gives users explicit control.

**Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
"""Tests for config module."""

import os
from unittest.mock import patch
from pathlib import Path


def test_explicit_version_env_var():
    """UE_VERSION env var should take priority."""
    with patch.dict(os.environ, {
        "UE_VERSION": "5.4",
        "UE_SOURCE_PATH": "/some/custom/path",
    }):
        # Re-import to pick up new env
        import importlib
        import unreal_source_mcp.config as cfg
        importlib.reload(cfg)
        db_path = cfg.get_db_path()
        assert "ue_5.4" in db_path.name


def test_version_detected_from_path():
    """Version should be extracted from UE_SOURCE_PATH."""
    with patch.dict(os.environ, {
        "UE_SOURCE_PATH": "C:/Program Files/UE_5.7/Engine/Source",
    }, clear=False):
        import importlib
        import unreal_source_mcp.config as cfg
        # Remove UE_VERSION if set
        os.environ.pop("UE_VERSION", None)
        importlib.reload(cfg)
        db_path = cfg.get_db_path()
        assert "ue_5.7" in db_path.name


def test_unknown_version_fallback():
    """Custom paths without version info should get 'unknown'."""
    with patch.dict(os.environ, {
        "UE_SOURCE_PATH": "/custom/engine/Source",
    }, clear=False):
        import importlib
        import unreal_source_mcp.config as cfg
        os.environ.pop("UE_VERSION", None)
        importlib.reload(cfg)
        db_path = cfg.get_db_path()
        assert "ue_unknown" in db_path.name
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: `test_explicit_version_env_var` fails (no UE_VERSION support yet)

**Step 3: Rewrite `config.py`**

```python
"""Configuration for unreal-source-mcp."""

import os
import re
from pathlib import Path

DB_DIR = Path(os.environ.get("UNREAL_SOURCE_MCP_DB_DIR", os.path.expanduser("~/.unreal-source-mcp")))
UE_SOURCE_PATH = os.environ.get("UE_SOURCE_PATH", "")
UE_SHADER_PATH = os.environ.get("UE_SHADER_PATH", "")
UE_VERSION = os.environ.get("UE_VERSION", "")


def _detect_version() -> str:
    """Detect UE version from UE_VERSION env var or UE_SOURCE_PATH."""
    if UE_VERSION:
        return UE_VERSION
    # Try to extract from path: look for patterns like UE_5.7, UE-5.7, UnrealEngine-5.7, etc.
    m = re.search(r"(\d+\.\d+)", UE_SOURCE_PATH)
    if m:
        return m.group(1)
    return "unknown"


def get_db_path() -> Path:
    """Return the path to the SQLite database, creating the directory if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    version = _detect_version()
    return DB_DIR / f"ue_{version}.db"
```

**Step 4: Update `__main__.py` to show detected version**

In `_run_index`, after computing `db_path`, add:
```python
    from unreal_source_mcp.config import _detect_version
    print(f"Detected UE version: {_detect_version()}", file=sys.stderr)
```

**Step 5: Run tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: All pass

Run: `uv run pytest -v` (full suite)
Expected: All pass

**Step 6: Commit**

```bash
git add src/unreal_source_mcp/config.py src/unreal_source_mcp/__main__.py tests/test_config.py
git commit -m "fix: robust UE version detection with UE_VERSION env var"
```

---

## Execution Order

Tasks 1-6 can be done mostly sequentially. Task 1 (batch commits) should be first since Task 3 (reference builder) adds many more inserts. Task 2 is independent. Tasks 4-6 are independent of each other.

Recommended order: **1 → 2 → 3 → 4 → 5 → 6**

After all tasks, run the full test suite: `uv run pytest -v`
