# MCP Server Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve the unreal-source-mcp server with 8 enhancements covering reference indexing, search capabilities, and tool ergonomics.

**Architecture:** Each improvement is a self-contained task modifying `server.py` (tool handlers), `db/queries.py` (SQL), and/or `indexer/reference_builder.py` (indexing). Tasks are ordered to minimize reindexing — tool-only changes first, then indexer changes.

**Tech Stack:** Python 3.11+, SQLite FTS5, tree-sitter-cpp, mcp SDK

---

### Task 1: Deduplicate forward declarations in read_source

Forward declarations like `struct FHitResult;` appear across 40+ files. The `read_source` tool should filter these out when the real definition exists.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `src/unreal_source_mcp/server.py:107-149`
- Create: `tests/fixtures/sample_ue_source/ForwardDecl.h` (test fixture)

**Step 1: Create test fixture with forward declarations**

Create `tests/fixtures/sample_ue_source/ForwardDecl.h`:
```cpp
#pragma once

// Forward declaration of FSampleData (defined in SampleActor.h)
struct FSampleData;

// Forward declaration of a class
class ASampleActor;
```

**Step 2: Write the failing test**

Add to `tests/test_server.py`:
```python
def test_read_source_filters_forward_declarations():
    """read_source should not return forward declarations when full definition exists."""
    result = server.read_source("FSampleData")
    # Should contain the real definition (multi-line struct)
    assert "UPROPERTY" in result or "Value" in result
    # Should NOT contain the single-line forward declaration
    lines = result.split("\n")
    forward_decl_lines = [l for l in lines if l.strip() == "struct FSampleData;"]
    assert len(forward_decl_lines) == 0, f"Forward declarations should be filtered out, found: {forward_decl_lines}"


def test_read_source_keeps_forward_decl_when_no_definition():
    """If only forward declarations exist (no full definition), keep one."""
    # Search for something that only exists as forward decl
    # This is a behavioral edge case — if no definition, at least show something
    result = server.read_source("ASampleActor")
    # The full class definition should be present
    assert "DoSomething" in result or "ENGINE_API" in result
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::test_read_source_filters_forward_declarations -v`
Expected: FAIL — forward declarations are currently included

**Step 4: Implement forward declaration filtering**

In `server.py`, modify the `read_source` function. After collecting all symbols, filter out forward declarations. Add a helper function `_is_forward_declaration`:

```python
import re

_FORWARD_DECL_RE = re.compile(r"^\s*(class|struct|enum)\s+\w[\w:]*\s*;")


def _is_forward_declaration(path: str, line_start: int, line_end: int) -> bool:
    """Check if a symbol entry is a single-line forward declaration."""
    if line_end - line_start > 1:
        return False
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        if line_start <= len(lines):
            return bool(_FORWARD_DECL_RE.match(lines[line_start - 1]))
    except OSError:
        pass
    return False
```

Then in `read_source`, after building the `symbols` list but before iterating, add filtering:

```python
    # Group symbols by name to detect forward declarations vs definitions
    # A symbol with line_end > line_start is a definition; line_end == line_start is likely a forward decl
    has_definition: dict[str, bool] = {}
    for sym in symbols:
        name = sym["name"]
        if sym["line_end"] - sym["line_start"] > 1:
            has_definition[name] = True

    parts: list[str] = []
    seen_files: set[tuple[int, int, int]] = set()

    for sym in symbols:
        file_id = sym["file_id"]
        line_start = sym["line_start"]
        line_end = sym["line_end"]
        key = (file_id, line_start, line_end)
        if key in seen_files:
            continue
        seen_files.add(key)

        filepath = _get_file_path(conn, file_id)

        # Filter forward declarations when a full definition exists
        if has_definition.get(sym["name"]) and line_end - line_start <= 1:
            if _is_forward_declaration(filepath, line_start, line_end):
                continue

        # Skip headers if not requested
        if not include_header and filepath.endswith(".h"):
            continue
        # ... rest unchanged
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add tests/fixtures/sample_ue_source/ForwardDecl.h tests/test_server.py src/unreal_source_mcp/server.py
git commit -m "feat: filter forward declarations from read_source results"
```

---

### Task 2: Fix find_references — add type reference extraction

The `ReferenceBuilder` only inserts `ref_kind="call"` references. Extend it to extract `type` references by walking the AST for `type_identifier` nodes that match known symbols.

**Files:**
- Modify: `tests/test_reference_builder.py`
- Modify: `src/unreal_source_mcp/indexer/reference_builder.py`
- Modify: `tests/fixtures/sample_ue_source/SampleActor.cpp` (add type usage)

**Step 1: Enhance the fixture to have clear type references**

Add a type usage to `tests/fixtures/sample_ue_source/SampleActor.cpp` — the file already has `UWorld* World = GetWorld();` on line 14, which is a type reference to `UWorld`. But `UWorld` is not in our fixture symbols. Instead, note that `DoSomething` already uses `float` (primitive, skip) and `ASampleActor` is used as a namespace qualifier.

Better: add to the end of `SampleActor.cpp`:
```cpp

void FreeFunctionUsingTypes()
{
    FSampleData Data;
    Data.Value = 1.0f;
    ASampleActor* Actor = nullptr;
}
```

This creates type references from `FreeFunctionUsingTypes` to `FSampleData` and `ASampleActor`.

**Step 2: Write the failing test**

Add to `tests/test_reference_builder.py`:
```python
def test_type_references_extracted(populated_db):
    """ReferenceBuilder should extract type references (e.g. FSampleData used in a function)."""
    row = populated_db.execute(
        'SELECT COUNT(*) AS cnt FROM "references" WHERE ref_kind = ?',
        ("type",),
    ).fetchone()
    assert row["cnt"] > 0, "Expected type references to be extracted"


def test_type_reference_to_fsampledata(populated_db):
    """FreeFunctionUsingTypes uses FSampleData — should have a type reference."""
    syms = get_symbols_by_name(populated_db, "FSampleData")
    assert len(syms) > 0
    all_refs = []
    for s in syms:
        all_refs.extend(get_references_to(populated_db, s["id"], ref_kind="type"))
    assert len(all_refs) > 0, f"Expected type references to FSampleData"
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_reference_builder.py::test_type_references_extracted -v`
Expected: FAIL — count is 0

**Step 4: Implement type reference extraction**

In `reference_builder.py`, add a `_extract_type_references` method to `ReferenceBuilder` and call it from `extract_references`:

```python
    def extract_references(self, path: Path, file_id: int) -> int:
        """Parse a C++ file and insert call + type references. Returns count."""
        try:
            source_bytes = path.read_bytes()
        except OSError:
            return 0

        tree = self._parser.parse(source_bytes)

        count = 0
        for func_node in self._find_nodes(tree.root_node, "function_definition"):
            caller_name = self._get_function_name(func_node)
            caller_id = self._resolve_symbol(caller_name)
            if caller_id is None:
                continue

            # Call references
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

            # Type references
            count += self._extract_type_references(func_node, caller_id, file_id)

        return count

    def _extract_type_references(
        self, func_node: Node, caller_id: int, file_id: int,
    ) -> int:
        """Extract type_identifier nodes that reference known symbols."""
        count = 0
        seen: set[int] = set()  # Deduplicate by target symbol id within a function

        for node in self._find_nodes(func_node, "type_identifier"):
            type_name = node.text.decode()
            type_id = self._resolve_symbol(type_name)
            if type_id is None or type_id == caller_id or type_id in seen:
                continue
            seen.add(type_id)

            line = node.start_point[0] + 1
            insert_reference(
                self._conn,
                from_symbol_id=caller_id,
                to_symbol_id=type_id,
                ref_kind="type",
                file_id=file_id,
                line=line,
            )
            count += 1

        return count
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_reference_builder.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add tests/fixtures/sample_ue_source/SampleActor.cpp tests/test_reference_builder.py src/unreal_source_mcp/indexer/reference_builder.py
git commit -m "feat: extract type references in ReferenceBuilder"
```

---

### Task 3: Add max_lines and members_only to read_source

Large classes like `UWorld` return hundreds of lines. Add `max_lines` for truncation and `members_only` to show only declarations.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `src/unreal_source_mcp/server.py:107-149`

**Step 1: Write failing tests**

Add to `tests/test_server.py`:
```python
def test_read_source_max_lines():
    """read_source with max_lines should truncate output."""
    full = server.read_source("ASampleActor")
    truncated = server.read_source("ASampleActor", max_lines=5)
    # Truncated should be shorter
    assert len(truncated.split("\n")) < len(full.split("\n"))
    assert "truncated" in truncated.lower()


def test_read_source_members_only():
    """read_source with members_only should show only member declarations."""
    result = server.read_source("ASampleActor", members_only=True)
    assert "DoSomething" in result
    assert "Health" in result
    # Should have signatures, not full implementation bodies
    assert "members" in result.lower() or "public" in result.lower() or "UFUNCTION" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::test_read_source_max_lines -v`
Expected: FAIL — `read_source` doesn't accept `max_lines` parameter

**Step 3: Implement max_lines and members_only**

Modify the `read_source` function signature and body in `server.py`:

```python
@mcp.tool()
def read_source(
    symbol: str,
    include_header: bool = True,
    max_lines: int = 0,
    members_only: bool = False,
) -> str:
    """Get the implementation source code for a class, function, or struct.

    Shows the actual source lines from disk with line numbers.
    For classes, shows both .h declaration and .cpp implementation if available.
    """
    conn = _get_conn()
    # ... existing symbol lookup ...

    parts: list[str] = []
    seen_files: set[tuple[int, int, int]] = set()
    total_lines = 0

    for sym in symbols:
        # ... existing dedup and forward-decl filtering ...

        if members_only and sym["kind"] in ("class", "struct"):
            source = _extract_members(filepath, line_start, line_end)
        else:
            source = _read_file_lines(filepath, line_start, line_end)

        parts.append(f"{header}\n{doc}{source}")

    result = "\n\n".join(parts) if parts else f"Found symbol '{symbol}' but could not read source files."

    # Apply max_lines truncation
    if max_lines > 0:
        lines = result.split("\n")
        if len(lines) > max_lines:
            result = "\n".join(lines[:max_lines])
            remaining = len(lines) - max_lines
            result += f"\n[...truncated, {remaining} more lines]"

    return result
```

Add the `_extract_members` helper:

```python
def _extract_members(path: str, start: int, end: int) -> str:
    """Extract member declarations from a class/struct, skipping inline implementations."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"[Error reading {path}]"

    start = max(1, start)
    end = min(len(lines), end)
    result_lines: list[str] = []
    brace_depth = 0
    in_body = False

    for i in range(start - 1, end):
        line = lines[i]
        stripped = line.strip()

        # Track braces to skip inline function bodies
        if in_body:
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0:
                in_body = False
                brace_depth = 0
            continue

        # Keep access specifiers, UE macros, and declarations
        if (stripped.startswith(("public:", "protected:", "private:", "GENERATED"))
            or stripped.startswith(("UFUNCTION", "UPROPERTY", "UENUM", "USTRUCT"))
            or stripped.startswith(("//", "/**", "*", "*/"))
            or stripped == ""
            or stripped == "{" or stripped == "}"
            or ";" in stripped):
            result_lines.append(f"{i+1:5d} | {line}")
        elif "{" in stripped:
            # Function with inline body — show signature, skip body
            sig_part = stripped.split("{")[0].rstrip()
            if sig_part:
                result_lines.append(f"{i+1:5d} | {sig_part};  // [inline body omitted]")
            brace_depth = stripped.count("{") - stripped.count("}")
            if brace_depth > 0:
                in_body = True

    return "\n".join(result_lines)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add tests/test_server.py src/unreal_source_mcp/server.py
git commit -m "feat: add max_lines and members_only params to read_source"
```

---

### Task 4: Add read_file tool for file-level reading by path

Add a new MCP tool that reads arbitrary source lines by file path and line range.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `src/unreal_source_mcp/server.py` (add new tool)
- Modify: `src/unreal_source_mcp/db/queries.py` (add path suffix search)

**Step 1: Write failing tests**

Add to `tests/test_server.py`:
```python
def test_read_file_by_full_path():
    """read_file should read lines from a file by its full path."""
    result = server.read_file(str(FIXTURES / "SampleActor.h"))
    assert "ASampleActor" in result
    assert "DoSomething" in result


def test_read_file_by_suffix():
    """read_file should resolve partial paths against the DB."""
    result = server.read_file("SampleActor.h")
    assert "ASampleActor" in result


def test_read_file_line_range():
    """read_file with start/end should return only those lines."""
    result = server.read_file(str(FIXTURES / "SampleActor.cpp"), start_line=11, end_line=19)
    assert "DoSomething" in result
    # Should not contain lines from outside the range
    assert "InternalHelper" not in result


def test_read_file_not_found():
    """read_file should return a helpful message for unknown paths."""
    result = server.read_file("NonExistent.h")
    assert "not found" in result.lower() or "No file" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::test_read_file_by_full_path -v`
Expected: FAIL — `server.read_file` doesn't exist

**Step 3: Add find_file_by_suffix query**

Add to `db/queries.py`:
```python
def find_file_by_suffix(conn: sqlite3.Connection, suffix: str) -> dict | None:
    """Find a file whose path ends with the given suffix."""
    row = conn.execute(
        "SELECT * FROM files WHERE path LIKE ? LIMIT 1",
        (f"%{suffix}",),
    ).fetchone()
    return _row_to_dict(row)
```

**Step 4: Add read_file tool**

Add to `server.py` after the `get_symbol_context` tool:
```python
# ── Tool 9: read_file ──────────────────────────────────────────────────

@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read source lines from a file by path.

    Supports full paths or partial paths (resolved against indexed files).
    Default: 200 lines from start_line. Set end_line to limit range.
    """
    conn = _get_conn()

    # Resolve the file path
    resolved_path: str | None = None

    # Try as absolute/full path first
    p = Path(path)
    if p.is_file():
        resolved_path = str(p)
    else:
        # Try DB lookup by exact path
        f = get_file_by_path(conn, path)
        if f:
            resolved_path = f["path"]
        else:
            # Try suffix match
            f = find_file_by_suffix(conn, path)
            if f:
                resolved_path = f["path"]

    if resolved_path is None:
        return f"No file found matching '{path}'."

    # Default end_line: 200 lines from start
    if end_line <= 0:
        end_line = start_line + 199

    # Get file metadata from DB if available
    header_parts: list[str] = []
    f = get_file_by_path(conn, resolved_path)
    if f:
        filepath_display = _short_path(resolved_path)
        header_parts.append(f"--- {filepath_display} (lines {start_line}-{end_line}) ---")
    else:
        header_parts.append(f"--- {path} (lines {start_line}-{end_line}) ---")

    source = _read_file_lines(resolved_path, start_line, end_line)
    return "\n".join(header_parts) + "\n" + source
```

Update imports at top of `server.py`:
```python
from unreal_source_mcp.db.queries import (
    ...,
    find_file_by_suffix,
    get_file_by_path,
)
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add tests/test_server.py src/unreal_source_mcp/server.py src/unreal_source_mcp/db/queries.py
git commit -m "feat: add read_file tool for file-level reading by path"
```

---

### Task 5: Add regex/substring search mode to search_source

FTS5 tokenization can't find multi-token patterns. Add `mode` parameter for regex/substring search.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `src/unreal_source_mcp/server.py:245-303`
- Modify: `src/unreal_source_mcp/db/queries.py`

**Step 1: Write failing tests**

Add to `tests/test_server.py`:
```python
def test_search_source_substring_mode():
    """search_source with mode='substring' should find exact multi-token patterns."""
    result = server.search_source("void DoSomething(float DeltaTime)", mode="substring")
    assert "DoSomething" in result


def test_search_source_regex_mode():
    """search_source with mode='regex' should find regex patterns."""
    result = server.search_source(r"void\s+\w+\(float", mode="regex")
    assert "DoSomething" in result or "SampleActor" in result


def test_search_source_fts_mode_default():
    """search_source with default mode should work as before (FTS)."""
    result = server.search_source("DoSomething")
    assert "DoSomething" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::test_search_source_substring_mode -v`
Expected: FAIL — `search_source` doesn't accept `mode` parameter

**Step 3: Add raw source chunk query**

Add to `db/queries.py`:
```python
def get_source_chunks(
    conn: sqlite3.Connection, keyword: str, scope: str = "all", limit: int = 500,
) -> list[dict]:
    """Fetch source_fts chunks containing a keyword (for post-filtering).

    Uses FTS to narrow candidates, returns raw text for regex/substring matching.
    """
    fts_query = _escape_fts(keyword)
    if scope == "all":
        rows = conn.execute(
            "SELECT f.file_id, f.line_number, f.text "
            "FROM source_fts f "
            "WHERE source_fts MATCH ? "
            "LIMIT ?",
            (fts_query, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT sf.file_id, sf.line_number, sf.text "
            "FROM source_fts sf "
            "JOIN files fi ON fi.id = sf.file_id "
            "WHERE source_fts MATCH ? AND fi.file_type = ? "
            "LIMIT ?",
            (fts_query, scope, limit),
        ).fetchall()
    return _rows_to_dicts(rows)
```

**Step 4: Implement regex/substring mode in search_source**

Modify `search_source` in `server.py`:

```python
@mcp.tool()
def search_source(
    query: str, scope: str = "all", limit: int = 20, mode: str = "fts",
) -> str:
    """Full-text search across Unreal Engine source code and shaders.

    scope: 'cpp' (headers+source), 'shaders' (usf/ush), 'all'
    mode: 'fts' (default, token-based), 'regex', 'substring'
    Returns both symbol matches and source line matches.
    """
    conn = _get_conn()
    parts: list[str] = []

    if mode in ("regex", "substring"):
        # Regex/substring: use FTS to narrow candidates, then filter
        parts.extend(_search_source_pattern(conn, query, scope, limit, mode))
    else:
        # Original FTS behavior
        # Symbol FTS search
        sym_results = search_symbols_fts(conn, query, limit=limit)
        if sym_results:
            parts.append("=== Symbol Matches ===")
            for sym in sym_results:
                filepath = _get_file_path(conn, sym["file_id"])
                sig = sym.get("signature") or ""
                parts.append(f"  [{sym['kind']}] {sym['qualified_name']} ({_short_path(filepath)}:{sym['line_start']})")
                if sig:
                    parts.append(f"         {sig}")

        # Source FTS search
        source_results = _get_source_fts_results(conn, query, scope, limit)
        if source_results:
            parts.append("\n=== Source Line Matches ===")
            parts.extend(_format_source_results(conn, source_results, limit))

    if not parts:
        return f"No results found for '{query}'."
    return "\n".join(parts)
```

Add helper functions:

```python
def _get_source_fts_results(
    conn: sqlite3.Connection, query: str, scope: str, limit: int,
) -> list[dict]:
    """Get source FTS results, handling scope mapping."""
    if scope == "cpp":
        results = search_source_fts(conn, query, limit=limit, scope="header")
        results += search_source_fts(conn, query, limit=limit, scope="source")
        results += search_source_fts(conn, query, limit=limit, scope="inline")
    elif scope == "shaders":
        results = search_source_fts(conn, query, limit=limit, scope="shader")
        results += search_source_fts(conn, query, limit=limit, scope="shader_header")
    else:
        results = search_source_fts(conn, query, limit=limit, scope="all")
    return results


def _format_source_results(
    conn: sqlite3.Connection, source_results: list[dict], limit: int,
) -> list[str]:
    """Format source FTS results with deduplication."""
    parts: list[str] = []
    seen: set[tuple[int, object]] = set()
    shown = 0
    for match in source_results:
        if shown >= limit:
            break
        fid = match["file_id"]
        line_num = match.get("line_number", "?")
        key = (fid, line_num)
        if key in seen:
            continue
        seen.add(key)
        filepath = _get_file_path(conn, fid)
        text = match.get("text", "").strip()
        if len(text) > 120:
            text = text[:120] + "..."
        parts.append(f"  {_short_path(filepath)}:{line_num}")
        parts.append(f"    {text}")
        shown += 1
    return parts


def _search_source_pattern(
    conn: sqlite3.Connection, pattern: str, scope: str, limit: int, mode: str,
) -> list[str]:
    """Search source using regex or substring matching."""
    import re as re_mod

    # Extract a keyword for FTS narrowing — use the longest alphanumeric word
    words = re_mod.findall(r'[a-zA-Z_]\w{2,}', pattern)
    if not words:
        return [f"Pattern must contain at least one keyword (3+ chars) for pre-filtering."]
    keyword = max(words, key=len)

    # Map scope to file_type values
    scopes: list[str] = []
    if scope == "cpp":
        scopes = ["header", "source", "inline"]
    elif scope == "shaders":
        scopes = ["shader", "shader_header"]
    else:
        scopes = ["all"]

    # Fetch candidate chunks
    from unreal_source_mcp.db.queries import get_source_chunks
    all_chunks: list[dict] = []
    for s in scopes:
        all_chunks.extend(get_source_chunks(conn, keyword, scope=s, limit=500))

    # Compile regex if needed
    if mode == "regex":
        try:
            compiled = re_mod.compile(pattern)
        except re_mod.error as e:
            return [f"Invalid regex: {e}"]
        match_fn = lambda text: compiled.search(text) is not None
    else:
        match_fn = lambda text: pattern in text

    # Filter and format
    parts: list[str] = [f"=== Pattern Matches ({mode}) ==="]
    seen: set[tuple[int, object]] = set()
    shown = 0
    for chunk in all_chunks:
        if shown >= limit:
            break
        text = chunk.get("text", "")
        if not match_fn(text):
            continue
        fid = chunk["file_id"]
        line_num = chunk.get("line_number", "?")
        key = (fid, line_num)
        if key in seen:
            continue
        seen.add(key)
        filepath = _get_file_path(conn, fid)

        # Find the specific matching line within the chunk
        for i, line in enumerate(text.split("\n")):
            if mode == "regex":
                if compiled.search(line):
                    display = line.strip()[:120]
                    actual_line = line_num + i if isinstance(line_num, int) else line_num
                    parts.append(f"  {_short_path(filepath)}:{actual_line}")
                    parts.append(f"    {display}")
                    shown += 1
                    break
            else:
                if pattern in line:
                    display = line.strip()[:120]
                    actual_line = line_num + i if isinstance(line_num, int) else line_num
                    parts.append(f"  {_short_path(filepath)}:{actual_line}")
                    parts.append(f"    {display}")
                    shown += 1
                    break

    if len(parts) == 1:
        return []  # No matches found
    return parts
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add tests/test_server.py src/unreal_source_mcp/server.py src/unreal_source_mcp/db/queries.py
git commit -m "feat: add regex/substring search mode to search_source"
```

---

### Task 6: Smarter "no callers" messaging

When `find_callers` returns nothing, provide context about why and search for delegate/function-pointer bindings.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `src/unreal_source_mcp/server.py:189-212`

**Step 1: Write failing tests**

Add to `tests/test_server.py`:
```python
def test_find_callers_smart_no_results_message():
    """find_callers should show a helpful message when no callers found."""
    result = server.find_callers("GetHealth")
    # GetHealth has no callers in our fixture
    assert "No direct" in result or "delegates" in result or "Blueprints" in result
    # Should NOT be just "No callers found"
    assert result != "No callers found for 'GetHealth'."
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::test_find_callers_smart_no_results_message -v`
Expected: FAIL — current message is exactly "No callers found for 'GetHealth'."

**Step 3: Implement smart messaging**

Modify the `find_callers` function in `server.py`. Replace the final `if not lines:` block:

```python
    if not lines:
        # Search for function pointer / delegate references like &ClassName::FunctionName
        func_name = symbols[0]["name"]
        qualified = symbols[0]["qualified_name"]
        delegate_pattern = f"&{qualified}" if "::" in qualified else f"&{func_name}"

        delegate_hits: list[str] = []
        delegate_results = search_source_fts(conn, delegate_pattern.replace("&", ""), limit=5)
        for match in delegate_results:
            text = match.get("text", "")
            if "&" in text and func_name in text:
                fid = match["file_id"]
                filepath = _get_file_path(conn, fid)
                line_num = match.get("line_number", "?")
                delegate_hits.append(f"  {_short_path(filepath)}:{line_num}")

        msg = (
            f"No direct C++ callers found for '{function}'. "
            "This function may be called via delegates, Blueprints, "
            "input bindings, or reflection (e.g. ProcessEvent)."
        )
        if delegate_hits:
            msg += "\n\nPossible indirect references (delegates/bindings):\n"
            msg += "\n".join(delegate_hits)
        return msg
```

Note: The search_source_fts is being repurposed here to search the `source_fts` table — but wait, it actually searches `source_fts`. We need to use the correct import. `search_source_fts` is already imported at the top of server.py.

Actually, `search_source_fts` searches the `source_fts` FTS table which contains source line text. We want to search for the function name pattern in source text, which is correct. The `&` won't be in the FTS tokens (stripped by `_escape_fts`), so we search for the function name and then check for `&` in the raw text.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add tests/test_server.py src/unreal_source_mcp/server.py
git commit -m "feat: smarter no-callers messaging with delegate search"
```

---

### Task 7: Module/path scoping on search_source

Add `module` and `path_filter` parameters to scope search results.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `src/unreal_source_mcp/db/queries.py`
- Modify: `src/unreal_source_mcp/server.py`

**Step 1: Write failing tests**

Add to `tests/test_server.py`:
```python
def test_search_source_module_filter():
    """search_source with module filter should only return results from that module."""
    result = server.search_source("ASampleActor", module="sample_ue_source")
    assert "ASampleActor" in result


def test_search_source_module_filter_excludes():
    """search_source with wrong module should return no results."""
    result = server.search_source("ASampleActor", module="NonExistentModule")
    assert "No results" in result


def test_search_source_path_filter():
    """search_source with path_filter should scope results."""
    result = server.search_source("ASampleActor", path_filter="SampleActor")
    assert "ASampleActor" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::test_search_source_module_filter -v`
Expected: FAIL — `search_source` doesn't accept `module` parameter

**Step 3: Add filtered FTS queries**

Add to `db/queries.py`:
```python
def search_symbols_fts_filtered(
    conn: sqlite3.Connection, query: str, limit: int = 20,
    kind: str | None = None, module: str | None = None, path_filter: str | None = None,
) -> list[dict]:
    """FTS symbol search with optional kind, module, and path filters."""
    fts_query = _escape_fts(query)
    sql = (
        "SELECT s.* FROM symbols_fts f "
        "JOIN symbols s ON s.id = f.rowid "
        "JOIN files fi ON fi.id = s.file_id "
    )
    conditions = ["symbols_fts MATCH ?"]
    params: list = [fts_query]

    if module:
        sql += "JOIN modules m ON m.id = fi.module_id "
        conditions.append("m.name = ?")
        params.append(module)
    if kind:
        conditions.append("s.kind = ?")
        params.append(kind)
    if path_filter:
        conditions.append("fi.path LIKE ?")
        params.append(f"%{path_filter}%")

    sql += "WHERE " + " AND ".join(conditions)
    sql += " ORDER BY bm25(symbols_fts) LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return _rows_to_dicts(rows)


def search_source_fts_filtered(
    conn: sqlite3.Connection, query: str, limit: int = 20, scope: str = "all",
    module: str | None = None, path_filter: str | None = None,
) -> list[dict]:
    """FTS source search with optional module and path filters."""
    fts_query = _escape_fts(query)
    conditions = ["source_fts MATCH ?"]
    params: list = [fts_query]

    if scope == "all" and not module and not path_filter:
        # Fast path — no joins needed
        rows = conn.execute(
            "SELECT f.file_id, f.line_number, f.text "
            "FROM source_fts f "
            "WHERE source_fts MATCH ? "
            "ORDER BY bm25(source_fts) LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        return _rows_to_dicts(rows)

    sql = (
        "SELECT sf.file_id, sf.line_number, sf.text "
        "FROM source_fts sf "
        "JOIN files fi ON fi.id = sf.file_id "
    )
    if module:
        sql += "JOIN modules m ON m.id = fi.module_id "
        conditions.append("m.name = ?")
        params.append(module)
    if scope != "all":
        conditions.append("fi.file_type = ?")
        params.append(scope)
    if path_filter:
        conditions.append("fi.path LIKE ?")
        params.append(f"%{path_filter}%")

    sql += "WHERE " + " AND ".join(conditions)
    sql += " ORDER BY bm25(source_fts) LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return _rows_to_dicts(rows)
```

**Step 4: Update search_source to use filtered queries**

Modify `search_source` in `server.py` to add `module` and `path_filter` parameters and use the new filtered queries:

```python
@mcp.tool()
def search_source(
    query: str, scope: str = "all", limit: int = 20, mode: str = "fts",
    module: str = "", path_filter: str = "",
) -> str:
    """Full-text search across Unreal Engine source code and shaders.

    scope: 'cpp' (headers+source), 'shaders' (usf/ush), 'all'
    mode: 'fts' (default, token-based), 'regex', 'substring'
    module: filter to files in this module (e.g. 'Engine')
    path_filter: filter to files whose path contains this string
    Returns both symbol matches and source line matches.
    """
```

In the FTS branch, replace `search_symbols_fts` and `search_source_fts` calls with the filtered versions, passing `module=module or None` and `path_filter=path_filter or None`.

Update imports in `server.py` to include the new query functions.

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add tests/test_server.py src/unreal_source_mcp/server.py src/unreal_source_mcp/db/queries.py
git commit -m "feat: add module and path_filter params to search_source"
```

---

### Task 8: Symbol kind filter on search_source

Add `symbol_kind` parameter to filter symbol FTS results by kind.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `src/unreal_source_mcp/server.py`

**Step 1: Write failing tests**

Add to `tests/test_server.py`:
```python
def test_search_source_symbol_kind_filter():
    """search_source with symbol_kind should filter symbol results."""
    result = server.search_source("ASampleActor", symbol_kind="class")
    assert "[class]" in result
    # Should not contain function-only results in symbol section
    lines = result.split("\n")
    symbol_section = []
    in_symbols = False
    for line in lines:
        if "Symbol Matches" in line:
            in_symbols = True
            continue
        if "Source Line" in line:
            break
        if in_symbols and line.strip().startswith("["):
            symbol_section.append(line)
    for line in symbol_section:
        assert "[class]" in line or "[struct]" not in line  # only class kind


def test_search_source_symbol_kind_function():
    """search_source with symbol_kind='function' filters to functions."""
    result = server.search_source("DoSomething", symbol_kind="function")
    assert "DoSomething" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py::test_search_source_symbol_kind_filter -v`
Expected: FAIL — `search_source` doesn't accept `symbol_kind`

**Step 3: Add symbol_kind parameter**

Update `search_source` signature to add `symbol_kind: str = ""` and pass it through to the filtered query:

```python
@mcp.tool()
def search_source(
    query: str, scope: str = "all", limit: int = 20, mode: str = "fts",
    module: str = "", path_filter: str = "", symbol_kind: str = "",
) -> str:
```

In the FTS branch, use the filtered query:
```python
        sym_results = search_symbols_fts_filtered(
            conn, query, limit=limit,
            kind=symbol_kind or None,
            module=module or None,
            path_filter=path_filter or None,
        )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add tests/test_server.py src/unreal_source_mcp/server.py
git commit -m "feat: add symbol_kind filter to search_source"
```

---

## Summary

| Task | Files Changed | Reindex? | Complexity |
|------|--------------|----------|------------|
| 1. Forward decl dedup | server.py, fixture | No | Low |
| 2. Type references | reference_builder.py, fixture | Yes | Medium |
| 3. read_source limits | server.py | No | Low |
| 4. read_file tool | server.py, queries.py | No | Low |
| 5. Regex search | server.py, queries.py | No | Medium |
| 6. Smart no-callers | server.py | No | Low |
| 7. Module scoping | server.py, queries.py | No | Medium |
| 8. Kind filter | server.py | No | Low |

After all tasks, run: `uv run pytest -v` to verify everything passes, then reindex once for Task 2's type references.
