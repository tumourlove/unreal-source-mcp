# MCP Server Improvements Design

**Date:** 2026-03-02
**Status:** Proposed

## Overview

Eight improvements to the unreal-source-mcp server, addressing reference indexing gaps, search capabilities, and tool ergonomics.

## 1. Fix find_references (HIGH)

**Problem:** `ReferenceBuilder` only inserts `ref_kind="call"` references. Types like `UCharacterMovementComponent` return zero references despite being used everywhere.

**Solution:** Extend `ReferenceBuilder.extract_references()` to emit three additional ref kinds:

- **`type` references:** Walk the AST for `type_identifier` and `qualified_identifier` nodes inside function bodies, variable declarations, and parameter lists. Resolve against `_sym_map`. Insert with `ref_kind="type"`.
- **`include` references:** During finalize, cross-reference the `includes` table with `files` table to resolve included paths to file IDs. Insert synthetic references linking includer file to included file. (Lower priority — includes are already queryable via the `includes` table.)
- **`override` references:** When processing a method `ChildClass::Method`, check if any ancestor class has a method with the same name. Insert `ref_kind="override"` from child method to parent method.

**Files changed:**
- `indexer/reference_builder.py` — add `_extract_type_references()`, `_extract_override_references()`
- `indexer/pipeline.py` — call override resolution after inheritance is resolved
- `db/queries.py` — add helper to look up ancestor methods

## 2. Deduplicate forward declarations in read_source (HIGH)

**Problem:** Querying `FHitResult` returns 40+ single-line forward declarations alongside the real definition.

**Solution:** In `read_source` tool handler (server.py):
1. After collecting all symbol matches, group by symbol name.
2. For each group, identify forward declarations: `line_start == line_end` AND source text matches `^\s*(class|struct|enum)\s+\w+\s*;`.
3. If a full definition exists (line_end - line_start > 0), drop all forward declarations for that name.
4. If ONLY forward declarations exist, keep one representative.

**Files changed:**
- `server.py` — `read_source` handler

## 3. Regex/substring search mode (MEDIUM)

**Problem:** `search_source` uses FTS5 tokenization, which can't find multi-token patterns like `virtual void Tick(float DeltaTime) override`.

**Solution:** Add `mode` parameter to `search_source`: `"fts"` (default), `"regex"`, `"substring"`.

For non-FTS modes:
- Query `source_fts` for a simplified keyword from the pattern (first significant word) to narrow candidates.
- Apply Python `re.search()` or `str.__contains__()` on the chunk text.
- For matches, read the actual source lines from disk to find the exact line number within the chunk.
- Return results with precise line numbers.

Fallback: if the pattern is too broad (>1000 candidate chunks), return an error suggesting a more specific pattern.

**Files changed:**
- `server.py` — `search_source` handler
- `db/queries.py` — add `search_source_raw()` for fetching chunk text for post-filtering

## 4. Smarter "no callers" messaging (LOW)

**Problem:** `find_callers` returns "No callers found" with no context about why (delegates, Blueprints, reflection).

**Solution:**
1. When no call references found, search `source_fts` for `&ClassName::FunctionName` pattern.
2. If hits found, show them as "Possible indirect references (delegates/bindings)."
3. If no hits either, return: "No direct C++ callers found. This function may be called via delegates, Blueprints, input bindings, or reflection (e.g. ProcessEvent)."

**Files changed:**
- `server.py` — `find_callers` handler
- `db/queries.py` — optional: add a convenience query for delegate pattern search

## 5. Limit/filter control on read_source (MEDIUM)

**Problem:** Huge classes like `UWorld` return hundreds of lines, but sometimes only member lists are needed.

**Solution:** Add two parameters:
- `max_lines: int = 0` — truncate output (0 = unlimited). When truncated, append `[...truncated, {remaining} more lines]`.
- `members_only: bool = False` — for class/struct symbols, parse the source to extract only member declarations (function signatures and variable declarations), skipping inline implementations and comments.

For `members_only`: read the source lines, use simple heuristic to identify member declarations (lines with `;` that aren't inside `{}` blocks), grouping by access specifier.

**Files changed:**
- `server.py` — `read_source` handler

## 6. File-level reading by path (MEDIUM)

**Problem:** No way to read arbitrary source lines by file path. Symbol lookups show "Character.cpp:1157" but you can't read around that line.

**Solution:** New MCP tool `read_file`:
```python
def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str
```
- Resolve `path` against the `files` table (match on full path or suffix).
- If no DB match, try resolving against `UE_SOURCE_PATH` / `UE_SHADER_PATH` directly.
- Read lines from disk using `_read_file_lines()`.
- Default `end_line=0` means 200 lines from start_line.
- Include file metadata header (module, file type) from DB if available.

**Files changed:**
- `server.py` — new `read_file` tool
- `db/queries.py` — add `find_file_by_suffix()` query

## 7. Module/path scoping on search_source (LOW)

**Problem:** Searches return results from the entire engine. No way to scope to a specific module or directory.

**Solution:** Add parameters:
- `module: str = ""` — filter to files belonging to this module name
- `path_filter: str = ""` — filter to files whose path contains this substring

Implementation: modify both symbol and source FTS queries to join with `files` and optionally `modules` tables, adding WHERE clauses.

**Files changed:**
- `db/queries.py` — new filtered variants of FTS queries
- `server.py` — `search_source` handler

## 8. Symbol kind filter on search_source (LOW)

**Problem:** Symbol search results include everything — classes, macros, variables, forward decls.

**Solution:** Add `symbol_kind: str = ""` parameter. When set, filter symbol FTS results to `symbols.kind = ?`.

**Files changed:**
- `db/queries.py` — add kind filter to `search_symbols_fts()`
- `server.py` — `search_source` handler

## Implementation Order

1. **#2 Forward declaration dedup** — pure tool-handler change, no reindex needed
2. **#1 Fix find_references** — requires reindex, biggest impact
3. **#5 read_source limits** — tool-handler only
4. **#6 read_file tool** — new tool, no reindex
5. **#3 Regex search** — tool + query changes
6. **#4 Smart no-callers** — small tool change
7. **#7 Module scoping** — query changes
8. **#8 Kind filter** — query changes

Items 2, 5, 6 can be done without reindexing. Item 1 requires a reindex. Items 3, 7, 8 are query/tool changes only.
