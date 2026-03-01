"""Insert and query helpers for the unreal-source-mcp database."""

from __future__ import annotations

import re
import sqlite3

# SQL table name — quoted to avoid any keyword conflicts
_REFS_TABLE = '"references"'


# ── Helpers ──────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def _escape_fts(query: str) -> str:
    """Prepare a user query for FTS5 MATCH.

    Strips special FTS characters, replaces :: with space,
    wraps each token in quotes with trailing * for prefix matching.
    """
    # Replace :: with space (common in C++ qualified names)
    q = query.replace("::", " ")
    # Strip FTS5 special chars
    q = re.sub(r'[^\w\s]', '', q)
    tokens = q.split()
    if not tokens:
        return '""'
    return " ".join(f'"{t}"*' for t in tokens)


# ── Insert helpers ───────────────────────────────────────────────────────

def insert_module(
    conn: sqlite3.Connection, *, name: str, path: str,
    module_type: str, build_cs_path: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO modules (name, path, module_type, build_cs_path) "
        "VALUES (?, ?, ?, ?)",
        (name, path, module_type, build_cs_path),
    )
    return cur.lastrowid


def insert_file(
    conn: sqlite3.Connection, *, path: str, module_id: int,
    file_type: str, line_count: int, last_modified: float = 0.0,
) -> int:
    cur = conn.execute(
        "INSERT INTO files (path, module_id, file_type, line_count, last_modified) "
        "VALUES (?, ?, ?, ?, ?)",
        (path, module_id, file_type, line_count, last_modified),
    )
    return cur.lastrowid


def insert_symbol(
    conn: sqlite3.Connection, *, name: str, qualified_name: str,
    kind: str, file_id: int, line_start: int, line_end: int,
    parent_symbol_id: int | None, access: str | None,
    signature: str | None, docstring: str | None, is_ue_macro: int = 0,
) -> int:
    cur = conn.execute(
        "INSERT INTO symbols (name, qualified_name, kind, file_id, line_start, "
        "line_end, parent_symbol_id, access, signature, docstring, is_ue_macro) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, qualified_name, kind, file_id, line_start, line_end,
         parent_symbol_id, access, signature, docstring, is_ue_macro),
    )
    return cur.lastrowid


def insert_inheritance(
    conn: sqlite3.Connection, *, child_id: int, parent_id: int,
) -> None:
    conn.execute(
        "INSERT INTO inheritance (child_id, parent_id) VALUES (?, ?)",
        (child_id, parent_id),
    )


def insert_reference(
    conn: sqlite3.Connection, *, from_symbol_id: int, to_symbol_id: int,
    ref_kind: str, file_id: int, line: int,
) -> None:
    conn.execute(
        f"INSERT INTO {_REFS_TABLE} (from_symbol_id, to_symbol_id, ref_kind, file_id, line) "
        "VALUES (?, ?, ?, ?, ?)",
        (from_symbol_id, to_symbol_id, ref_kind, file_id, line),
    )


def insert_include(
    conn: sqlite3.Connection, *, file_id: int, included_path: str, line: int,
) -> None:
    conn.execute(
        "INSERT INTO includes (file_id, included_path, line) VALUES (?, ?, ?)",
        (file_id, included_path, line),
    )


# ── Query helpers ────────────────────────────────────────────────────────

def get_symbol_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    """Exact match on qualified_name first, then fall back to name."""
    row = conn.execute(
        "SELECT * FROM symbols WHERE qualified_name = ? LIMIT 1", (name,)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM symbols WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    return _row_to_dict(row)


def get_symbol_by_id(conn: sqlite3.Connection, symbol_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM symbols WHERE id = ?", (symbol_id,)
    ).fetchone()
    return _row_to_dict(row)


def get_symbols_by_name(
    conn: sqlite3.Connection, name: str, kind: str | None = None,
) -> list[dict]:
    if kind:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name = ? AND kind = ?", (name, kind)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name = ?", (name,)
        ).fetchall()
    return _rows_to_dicts(rows)


def search_symbols_fts(
    conn: sqlite3.Connection, query: str, limit: int = 20,
) -> list[dict]:
    fts_query = _escape_fts(query)
    rows = conn.execute(
        "SELECT s.* FROM symbols_fts f "
        "JOIN symbols s ON s.id = f.rowid "
        "WHERE symbols_fts MATCH ? "
        "ORDER BY bm25(symbols_fts) "
        "LIMIT ?",
        (fts_query, limit),
    ).fetchall()
    return _rows_to_dicts(rows)


def search_source_fts(
    conn: sqlite3.Connection, query: str, limit: int = 20, scope: str = "all",
) -> list[dict]:
    """Search source text via FTS5.

    scope: 'all', 'header', 'source' -- filters by files.file_type.
    """
    fts_query = _escape_fts(query)
    if scope == "all":
        rows = conn.execute(
            "SELECT f.file_id, f.line_number, f.text "
            "FROM source_fts f "
            "WHERE source_fts MATCH ? "
            "ORDER BY bm25(source_fts) "
            "LIMIT ?",
            (fts_query, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT sf.file_id, sf.line_number, sf.text "
            "FROM source_fts sf "
            "JOIN files fi ON fi.id = sf.file_id "
            "WHERE source_fts MATCH ? AND fi.file_type = ? "
            "ORDER BY bm25(source_fts) "
            "LIMIT ?",
            (fts_query, scope, limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_file_by_id(conn: sqlite3.Connection, file_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return _row_to_dict(row)


def get_file_by_path(conn: sqlite3.Connection, path: str) -> dict | None:
    row = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
    return _row_to_dict(row)


def get_module_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM modules WHERE name = ?", (name,)).fetchone()
    return _row_to_dict(row)


def get_inheritance_parents(conn: sqlite3.Connection, child_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.* FROM inheritance i "
        "JOIN symbols s ON s.id = i.parent_id "
        "WHERE i.child_id = ?",
        (child_id,),
    ).fetchall()
    return _rows_to_dicts(rows)


def get_inheritance_children(conn: sqlite3.Connection, parent_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.* FROM inheritance i "
        "JOIN symbols s ON s.id = i.child_id "
        "WHERE i.parent_id = ?",
        (parent_id,),
    ).fetchall()
    return _rows_to_dicts(rows)


def get_references_to(
    conn: sqlite3.Connection, symbol_id: int,
    ref_kind: str | None = None, limit: int = 50,
) -> list[dict]:
    """Get references pointing TO this symbol, with from_name and path."""
    if ref_kind:
        rows = conn.execute(
            f"SELECT r.*, s.name AS from_name, f.path "
            f"FROM {_REFS_TABLE} r "
            f"JOIN symbols s ON s.id = r.from_symbol_id "
            f"JOIN files f ON f.id = r.file_id "
            f"WHERE r.to_symbol_id = ? AND r.ref_kind = ? "
            f"LIMIT ?",
            (symbol_id, ref_kind, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT r.*, s.name AS from_name, f.path "
            f"FROM {_REFS_TABLE} r "
            f"JOIN symbols s ON s.id = r.from_symbol_id "
            f"JOIN files f ON f.id = r.file_id "
            f"WHERE r.to_symbol_id = ? "
            f"LIMIT ?",
            (symbol_id, limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_references_from(
    conn: sqlite3.Connection, symbol_id: int,
    ref_kind: str | None = None, limit: int = 50,
) -> list[dict]:
    """Get references FROM this symbol, with to_name and path."""
    if ref_kind:
        rows = conn.execute(
            f"SELECT r.*, s.name AS to_name, f.path "
            f"FROM {_REFS_TABLE} r "
            f"JOIN symbols s ON s.id = r.to_symbol_id "
            f"JOIN files f ON f.id = r.file_id "
            f"WHERE r.from_symbol_id = ? AND r.ref_kind = ? "
            f"LIMIT ?",
            (symbol_id, ref_kind, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT r.*, s.name AS to_name, f.path "
            f"FROM {_REFS_TABLE} r "
            f"JOIN symbols s ON s.id = r.to_symbol_id "
            f"JOIN files f ON f.id = r.file_id "
            f"WHERE r.from_symbol_id = ? "
            f"LIMIT ?",
            (symbol_id, limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_symbols_in_module(
    conn: sqlite3.Connection, module_name: str,
    kind: str | None = None, limit: int = 200,
) -> list[dict]:
    if kind:
        rows = conn.execute(
            "SELECT s.* FROM symbols s "
            "JOIN files f ON f.id = s.file_id "
            "JOIN modules m ON m.id = f.module_id "
            "WHERE m.name = ? AND s.kind = ? "
            "LIMIT ?",
            (module_name, kind, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT s.* FROM symbols s "
            "JOIN files f ON f.id = s.file_id "
            "JOIN modules m ON m.id = f.module_id "
            "WHERE m.name = ? "
            "LIMIT ?",
            (module_name, limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_module_stats(conn: sqlite3.Connection, module_name: str) -> dict | None:
    """Return file_count and symbol_counts by kind for a module."""
    mod = get_module_by_name(conn, module_name)
    if mod is None:
        return None

    file_count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE module_id = ?", (mod["id"],)
    ).fetchone()[0]

    kind_rows = conn.execute(
        "SELECT s.kind, COUNT(*) as cnt FROM symbols s "
        "JOIN files f ON f.id = s.file_id "
        "WHERE f.module_id = ? "
        "GROUP BY s.kind",
        (mod["id"],),
    ).fetchall()

    symbol_counts = {row["kind"]: row["cnt"] for row in kind_rows}

    return {
        "module": mod,
        "file_count": file_count,
        "symbol_counts": symbol_counts,
    }
