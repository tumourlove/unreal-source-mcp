# unreal-source-mcp

Deep Unreal Engine source intelligence for AI development via [Model Context Protocol](https://modelcontextprotocol.io/).

Indexes all UE C++ and HLSL source code into a local SQLite database and exposes structural queries — class hierarchies, call graphs, cross-references, and full-text search across 80k+ files and 1M+ symbols — as MCP tools for AI coding assistants like Claude Code.

## Why?

AI assistants hallucinate engine APIs, guess at implementation patterns, and can't see how Epic actually built things. This server gives them deep structural access to the real engine source — so they write code that matches how UE actually works, not how they imagine it works.

**Complements** (does not replace):
- [unreal-project-mcp](https://github.com/tumourlove/unreal-project-mcp) — Project-level source intelligence (your C++ code)
- [unreal-editor-mcp](https://github.com/tumourlove/unreal-editor-mcp) — Build diagnostics and editor log tools (Live Coding, error parsing, log search)
- [unreal-material-mcp](https://github.com/tumourlove/unreal-material-mcp) — Material graph intelligence and editing (expressions, connections, parameters, instances, graph manipulation)
- [unreal-blueprint-mcp](https://github.com/tumourlove/unreal-blueprint-mcp) — Blueprint graph reading (nodes, pins, connections, execution flow)
- [unreal-config-mcp](https://github.com/tumourlove/unreal-config-mcp) — Config/INI intelligence (resolve inheritance chains, search settings, diff from defaults, explain CVars)
- [unreal-animation-mcp](https://github.com/tumourlove/unreal-animation-mcp) — Animation data inspector and editor (sequences, montages, blend spaces, ABPs, skeletons, 62 tools)
- [unreal-api-mcp](https://github.com/nicobailon/unreal-api-mcp) by [Nico Bailon](https://github.com/nicobailon) — API surface lookup (signatures, #include paths, deprecation warnings)

Together these servers give AI agents full-stack UE understanding: engine internals, API surface, your project code, build/runtime feedback, Blueprint graph data, config/INI intelligence, material graph inspection + editing, and animation data inspection + editing.

## Quick Start

### Install from GitHub

```bash
uvx --from git+https://github.com/tumourlove/unreal-source-mcp unreal-source-mcp --index
```

### Claude Code Configuration

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "unreal-source": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/tumourlove/unreal-source-mcp", "unreal-source-mcp"],
      "env": {
        "UE_SOURCE_PATH": "C:/Path/To/UE_5.x/Engine/Source",
        "UE_SHADER_PATH": "C:/Path/To/UE_5.x/Engine/Shaders"
      }
    }
  }
}
```

Or run from local source during development:

```json
{
  "mcpServers": {
    "unreal-source": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/unreal-source-mcp", "python", "-m", "unreal_source_mcp"],
      "env": {
        "UE_SOURCE_PATH": "C:/Path/To/UE_5.x/Engine/Source",
        "UE_SHADER_PATH": "C:/Path/To/UE_5.x/Engine/Shaders"
      }
    }
  }
}
```

The server auto-indexes on first run (~10-15 min). All subsequent queries are instant.

## Tools

| Tool | Description |
|------|-------------|
| `read_source` | Get implementation source code for a symbol with line numbers. Shows both .h and .cpp. |
| `find_references` | Find all usage sites of a symbol (calls, includes, type references). |
| `find_callers` | Find all functions that call a given function. |
| `find_callees` | Find all functions called by a given function. |
| `search_source` | Full-text search across C++ and/or shader source. Supports FTS, regex, and substring modes. |
| `get_class_hierarchy` | Show the inheritance tree for a class — ancestors, descendants, or both. |
| `get_module_info` | Module statistics: file count, symbol counts by kind, key classes. |
| `get_symbol_context` | Symbol definition with surrounding context and doc comments. |
| `read_file` | Read source lines from a file by path. |

## CLI

```bash
# Index engine source (first time)
unreal-source-mcp --index

# Rebuild from scratch
unreal-source-mcp --reindex

# Run as MCP server (default, used by Claude Code)
unreal-source-mcp
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `UE_SOURCE_PATH` | Yes | Path to `Engine/Source` in your UE install |
| `UE_SHADER_PATH` | No | Path to `Engine/Shaders` (enables shader indexing) |
| `UE_VERSION` | No | Override version detection (e.g. `5.7`) |

## How It Works

1. **Discovery** — Walks `Engine/Source/` for all Runtime, Editor, Developer, and plugin modules. Optionally indexes `Engine/Shaders/` for HLSL (usf/ush).

2. **Parsing** — Uses [tree-sitter](https://tree-sitter.github.io/) with the C++ grammar to build ASTs. Handles UE macros (UCLASS, UFUNCTION, UPROPERTY, etc.) with regex fallback for misparsed nodes.

3. **Storage** — SQLite with FTS5 full-text search. 80k+ files, 1M+ symbols indexed. Database is versioned per UE version at `~/.unreal-source-mcp/ue_{version}.db`.

4. **Serving** — FastMCP server exposes 9 tools over stdio. Claude Code manages the server lifecycle automatically.

## What Gets Indexed

- All engine C++ source (Runtime, Editor, Developer, built-in plugins)
- HLSL shader source (usf/ush files)
- Classes, structs, enums with inheritance and UE macro metadata
- Functions (declarations and definitions with qualified names)
- Call references, type references, include relationships
- Docstrings and symbol context

## Add to Your CLAUDE.md

```markdown
## Unreal Engine Source Intelligence (unreal-source MCP)

Use `unreal-source` MCP tools to read actual engine source code, trace call graphs,
and explore class hierarchies. **Use this when you need to understand HOW something
works, not just its API signature.**

| Tool | When |
|------|------|
| `read_source` | Read the actual implementation of a class/function |
| `find_references` | Find all usage sites of a symbol |
| `find_callers` | What calls this function? |
| `find_callees` | What does this function call? |
| `search_source` | Full-text search across C++ and shader source |
| `get_class_hierarchy` | Inheritance tree (ancestors/descendants) |
| `get_module_info` | Module stats, file counts, key classes |
| `get_symbol_context` | Symbol definition with surrounding context |

**Rules:** Use `read_source` to understand engine internals before reimplementing
patterns. Use `find_callers` to see how Epic uses their own APIs.
```

## Development

```bash
# Clone and install
git clone https://github.com/tumourlove/unreal-source-mcp.git
cd unreal-source-mcp
uv sync

# Run tests
uv run pytest -v

# Run locally
UE_SOURCE_PATH="/path/to/Engine/Source" uv run python -m unreal_source_mcp
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A local Unreal Engine source installation

## License

MIT
