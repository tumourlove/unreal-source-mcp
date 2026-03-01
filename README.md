# unreal-source-mcp

Deep Unreal Engine source intelligence for AI agents.

An MCP server that indexes all UE C++ and HLSL source code into a local SQLite database, giving AI agents structural understanding of the engine — class hierarchies, call graphs, cross-references, and full-text search across 80k+ files and 1M+ symbols.

## What it does

Point it at your local Unreal Engine installation and it indexes everything on first run (~10-15 min). After that, queries are instant.

- **Read source** — Get the actual implementation code for any class, function, or struct
- **Class hierarchy** — See inheritance trees (ancestors and descendants) across all modules
- **Call graphs** — Who calls this function? What does it call internally?
- **Cross-references** — Find every usage of a symbol across the entire engine
- **Full-text search** — Search across C++ source, headers, and HLSL shaders
- **Module info** — File counts, symbol breakdowns, key classes per module

Works with any UE version — it detects the version from your install path and creates a versioned database.

## Install

### Claude Code (recommended)

Add to your MCP config (`~/.claude.json` or project `.mcp.json`):

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

Replace the paths with your actual Unreal Engine install location.

### From source

```bash
git clone https://github.com/tumourlove/unreal-source-mcp
cd unreal-source-mcp
uv sync
```

Then configure MCP to run from the local directory:

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

## First run

The first time an MCP tool is called, it automatically indexes your UE source. This takes ~10-15 minutes and creates a database at `~/.unreal-source-mcp/ue_{version}.db`. All subsequent calls are instant.

You can also index manually:

```bash
UE_SOURCE_PATH="C:/Path/To/UE_5.x/Engine/Source" \
UE_SHADER_PATH="C:/Path/To/UE_5.x/Engine/Shaders" \
uvx --from git+https://github.com/tumourlove/unreal-source-mcp unreal-source-mcp --index
```

Use `--reindex` to rebuild from scratch.

## Add to your CLAUDE.md

Add this to your project's `CLAUDE.md` so the agent knows when and how to use the tools:

```markdown
## Unreal Engine Source Intelligence (unreal-source MCP)

Use `unreal-source` MCP tools to read actual engine source code, trace call graphs, and explore class hierarchies. **Use this when you need to understand HOW something works, not just its API signature.**

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

**Rules:** Use `read_source` to understand engine internals before reimplementing patterns. Use `find_callers` to see how Epic uses their own APIs. Covers engine Runtime/Editor/Developer + plugins + shaders (1M+ symbols indexed).
```

## Tools

| Tool | Description |
|------|-------------|
| `read_source` | Get implementation source code for a symbol with line numbers |
| `find_references` | Find all usage sites (calls, includes, type references) |
| `find_callers` | What functions call this function? |
| `find_callees` | What does this function call internally? |
| `search_source` | Full-text search across C++ and/or shader source |
| `get_class_hierarchy` | Inheritance tree — ancestors and descendants |
| `get_module_info` | Module stats: file count, symbol counts, key classes |
| `get_symbol_context` | Symbol definition with surrounding context and doc comments |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `UE_SOURCE_PATH` | Yes | Path to `Engine/Source` in your UE install |
| `UE_SHADER_PATH` | No | Path to `Engine/Shaders` (enables shader indexing) |
| `UE_VERSION` | No | Override version detection (e.g. `5.7`) |

## Requirements

- Python 3.11+
- A local Unreal Engine source installation

## License

MIT
