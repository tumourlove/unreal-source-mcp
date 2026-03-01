"""Configuration for unreal-source-mcp."""

import os
from pathlib import Path

DB_DIR = Path(os.environ.get("UNREAL_SOURCE_MCP_DB_DIR", os.path.expanduser("~/.unreal-source-mcp")))
UE_SOURCE_PATH = os.environ.get("UE_SOURCE_PATH", "")
UE_SHADER_PATH = os.environ.get("UE_SHADER_PATH", "")


def get_db_path() -> Path:
    """Return the path to the SQLite database, creating the directory if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    version = "unknown"
    for v in ("5.7", "5.6", "5.5"):
        if f"UE_{v}" in UE_SOURCE_PATH:
            version = v
            break
    return DB_DIR / f"ue_{version}.db"
