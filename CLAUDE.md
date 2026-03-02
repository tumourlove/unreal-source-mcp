# CLAUDE.md — unreal-source-mcp

## Project Overview

**unreal-source-mcp** — Deep Unreal Engine source intelligence for AI agents.

An MCP (Model Context Protocol) server that indexes all UE C++ and HLSL source code into a SQLite database, providing structural queries (class hierarchy, call graphs, cross-references) and full-text search across the entire engine codebase.

**Complements** (does not replace):
- `unreal-api-mcp` — API surface (signatures, includes, deprecation)
- `Agent Integration Kit` — Editor control (Blueprints, assets, Python execution)

**We provide:** Implementation-level understanding — actual source code, cross-references, call graphs, and patterns.

## Tech Stack

- **Language:** Python 3.11+
- **Parser:** tree-sitter + tree-sitter-cpp (C++ AST), regex (HLSL shaders)
- **Storage:** SQLite + FTS5 full-text search
- **MCP SDK:** `mcp` Python package
- **Distribution:** PyPI via `uvx unreal-source-mcp`
- **Package manager:** `uv` (for dev and build)

## Project Structure

```
unreal-source-mcp/
├── pyproject.toml              # Package config, dependencies, entry point
├── CLAUDE.md                   # This file
├── README.md                   # User-facing docs
├── LICENSE                     # MIT
├── src/
│   └── unreal_source_mcp/
│       ├── __init__.py         # Version
│       ├── __main__.py         # Entry point (uvx runs this)
│       ├── server.py           # MCP server + tool registration
│       ├── indexer/
│       │   ├── __init__.py
│       │   ├── pipeline.py     # Orchestrates full indexing run
│       │   ├── cpp_parser.py   # tree-sitter C++ symbol/reference extraction
│       │   ├── shader_parser.py# Regex-based HLSL extraction
│       │   ├── ue_macros.py    # UCLASS/UFUNCTION/UPROPERTY macro handling
│       │   └── reference_builder.py  # Cross-reference extraction
│       ├── db/
│       │   ├── __init__.py
│       │   ├── schema.py       # SQLite table definitions + migrations
│       │   └── queries.py      # All SQL queries (no inline SQL elsewhere)
│       └── tools/
│           ├── __init__.py
│           ├── read_source.py
│           ├── find_references.py
│           ├── search_source.py
│           ├── class_hierarchy.py
│           ├── module_info.py
│           └── symbol_context.py
└── tests/
    ├── test_cpp_parser.py
    ├── test_shader_parser.py
    ├── test_queries.py
    └── fixtures/
        └── sample_ue_source/   # Small UE-like .h/.cpp files for testing
```

## Build & Run

```bash
# Install dev dependencies
uv sync

# Run the MCP server locally
uv run python -m unreal_source_mcp

# Run tests
uv run pytest

# Build and index (first time)
UE_SOURCE_PATH="C:/Program Files (x86)/UE_5.7/Engine/Source" \
UE_SHADER_PATH="C:/Program Files (x86)/UE_5.7/Engine/Shaders" \
uv run python -m unreal_source_mcp --index

# Install globally via uvx (after publishing)
uvx unreal-source-mcp
```

## MCP Configuration (for Claude Code)

```json
{
  "mcpServers": {
    "unreal-source": {
      "command": "uvx",
      "args": ["unreal-source-mcp"],
      "env": {
        "UE_SOURCE_PATH": "C:/Program Files (x86)/UE_5.7/Engine/Source",
        "UE_SHADER_PATH": "C:/Program Files (x86)/UE_5.7/Engine/Shaders"
      }
    }
  }
}
```

## MCP Tools (8)

| Tool | Purpose |
|------|---------|
| `read_source` | Get implementation code for a symbol (class, function, struct) |
| `find_references` | Find all usage sites of a symbol across the engine |
| `find_callers` | What functions call this function? |
| `find_callees` | What does this function call internally? |
| `search_source` | Full-text search across C++ and/or shader source |
| `get_class_hierarchy` | Inheritance tree with virtual function overrides |
| `get_module_info` | Module contents, dependencies, statistics |
| `get_symbol_context` | Symbol definition with surrounding context and doc comments |

## Database

- **Location:** `~/.unreal-source-mcp/ue_{version}.db`
- **Schema:** See `src/unreal_source_mcp/db/schema.py`
- **Size:** ~200-500MB per engine version
- **Index time:** <15 minutes target on modern hardware

### Key Tables
- `files` — path, module, file_type (cpp/h/usf/ush)
- `symbols` — name, qualified_name, kind, signature, docstring, line range
- `inheritance` — class hierarchy relationships
- `references` — cross-references (call/use/include/override)
- `modules` — Runtime/Editor/Plugin module metadata
- `source_fts` / `symbols_fts` — FTS5 full-text search

## Coding Conventions

- **All SQL lives in `db/queries.py`** — no inline SQL in tool handlers
- **Tool handlers are thin** — validate params, call query, format response
- **UE macro handling lives in `indexer/cpp_parser.py`** — `UE_MACROS` set, `_try_get_ue_macro()`, `_try_get_ue_macro_field()`, etc.
- Follow standard Python conventions: snake_case, type hints, docstrings on public functions
- Use `logging` module, not print statements
- Tests use pytest with fixtures in `tests/fixtures/`
- Keep dependencies minimal — stdlib SQLite, tree-sitter, mcp SDK, and that's it

## UE-Specific Parsing Notes

- `UCLASS()`, `UFUNCTION()`, `UPROPERTY()`, `UENUM()`, `USTRUCT()` are macros that precede declarations — parse them as annotations on the following symbol
- `GENERATED_BODY()` / `GENERATED_UCLASS_BODY()` expand to compiler-generated code — skip
- `DECLARE_DELEGATE*`, `DECLARE_DYNAMIC_MULTICAST_DELEGATE*` — extract as delegate symbols
- `TEXT("...")` and `LOCTEXT(...)` are string macros — don't confuse with function calls
- Shader files use `#include "/Engine/..."` paths — resolve relative to shader root

## Known Limitations / Future Work

- **Incremental indexing:** The `last_modified` column exists in the `files` table and `st_mtime` is already stored during indexing. A future enhancement should compare mtimes and skip unchanged files, with careful handling of cross-file reference invalidation when a single file changes.
- **Method call type inference:** `->Method()` calls resolve to the unqualified method name when the object type can't be determined from local variable declarations. Full type inference (member variables, function parameters, chained calls) is not implemented.

## Design Doc

Full design: `D:/Unreal Projects/Leviathan/Docs/plans/2026-03-01-unreal-source-mcp-design.md`
