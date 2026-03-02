"""Indexing pipeline — discovers, parses, and stores UE source into SQLite."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from unreal_source_mcp.db.queries import (
    get_file_by_path,
    insert_file,
    insert_include,
    insert_inheritance,
    insert_module,
    insert_symbol,
)
from unreal_source_mcp.indexer.cpp_parser import CppParser
from unreal_source_mcp.indexer.reference_builder import ReferenceBuilder
from unreal_source_mcp.indexer.shader_parser import ShaderParser

logger = logging.getLogger(__name__)

_CPP_EXTENSIONS = {".h", ".cpp", ".inl"}
_SHADER_EXTENSIONS = {".usf", ".ush"}
_EXT_TO_FILETYPE = {
    ".h": "header",
    ".cpp": "source",
    ".inl": "inline",
    ".usf": "shader",
    ".ush": "shader_header",
}


class IndexingPipeline:
    """Walks an Unreal Engine source tree, parses files, and stores results."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cpp_parser = CppParser()
        self._shader_parser = ShaderParser()
        self._symbol_name_to_id: dict[str, Any] = {}
        self._symbol_spans: dict[str, tuple[int, int]] = {}  # name → (line_start, line_end)
        self._class_name_to_id: dict[str, int] = {}  # class/struct only — for inheritance
        self._class_spans: dict[str, tuple[int, int]] = {}  # class name → (line_start, line_end)
        conn.commit()
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

    # ── Public API ──────────────────────────────────────────────────────

    def index_directory(
        self,
        path: Path,
        module_name: str | None = None,
        module_type: str = "Runtime",
        *,
        finalize: bool = True,
    ) -> dict[str, Any]:
        """Index all C++/shader files under *path*.

        If *finalize* is True (default), resolves inheritance and extracts
        cross-references after parsing.  Set to False when calling from
        index_engine, which does a single global finalize at the end.

        Returns stats: {files_processed, symbols_extracted, errors}.
        """
        path = Path(path)
        if module_name is None:
            module_name = path.name

        mod_id = insert_module(
            self._conn,
            name=module_name,
            path=str(path),
            module_type=module_type,
        )

        files_processed = 0
        symbols_extracted = 0
        errors = 0

        for dirpath, _dirnames, filenames in os.walk(path):
            for fname in filenames:
                fpath = Path(dirpath) / fname
                ext = fpath.suffix.lower()
                try:
                    if ext in _CPP_EXTENSIONS:
                        n = self._index_cpp_file(fpath, mod_id)
                        symbols_extracted += n
                        files_processed += 1
                    elif ext in _SHADER_EXTENSIONS:
                        n = self._index_shader_file(fpath, mod_id)
                        symbols_extracted += n
                        files_processed += 1
                except Exception:
                    logger.warning("Error indexing %s", fpath, exc_info=True)
                    errors += 1

        self._conn.commit()

        if finalize:
            self._finalize()

        return {
            "files_processed": files_processed,
            "symbols_extracted": symbols_extracted,
            "errors": errors,
        }

    def index_engine(
        self,
        source_path: Path,
        shader_path: Path | None = None,
        on_progress: Callable[[str, int, int, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Index an entire Unreal Engine source tree.

        Walks Engine/Source/{Runtime,Editor,Developer,Programs} and Plugins.

        *on_progress*, if provided, is called after each module:
            on_progress(module_name, module_index, total_modules, files_so_far, symbols_so_far)
        """
        source_path = Path(source_path)
        total_files = 0
        total_symbols = 0
        total_errors = 0

        # First pass: discover all modules so we can report total count
        modules: list[tuple[Path, str, str]] = []  # (path, name, type)

        categories = ["Runtime", "Editor", "Developer", "Programs"]
        for category in categories:
            cat_dir = source_path / category
            if not cat_dir.is_dir():
                continue
            for sub in sorted(cat_dir.iterdir()):
                if sub.is_dir():
                    modules.append((sub, sub.name, category))

        plugins_dir = source_path.parent / "Plugins"
        if plugins_dir.is_dir():
            for source_dir in sorted(plugins_dir.rglob("Source")):
                if source_dir.is_dir():
                    modules.append((source_dir, source_dir.parent.name, "Plugin"))

        if shader_path and shader_path.is_dir():
            modules.append((shader_path, "Shaders", "Shaders"))

        total_modules = len(modules)

        for i, (mod_path, mod_name, mod_type) in enumerate(modules):
            stats = self.index_directory(
                mod_path,
                module_name=mod_name,
                module_type=mod_type,
                finalize=False,
            )
            total_files += stats["files_processed"]
            total_symbols += stats["symbols_extracted"]
            total_errors += stats["errors"]

            if on_progress:
                on_progress(mod_name, i + 1, total_modules, total_files, total_symbols)

        # Global finalize — resolve inheritance and references across all modules
        if on_progress:
            on_progress("Finalizing (inheritance + references)...", total_modules, total_modules, total_files, total_symbols)
        self._finalize()

        return {
            "files_processed": total_files,
            "symbols_extracted": total_symbols,
            "errors": total_errors,
        }

    def _finalize(self) -> None:
        """Resolve inheritance and extract cross-references globally."""
        self._resolve_inheritance()
        self._conn.commit()

        # Extract cross-references from all indexed C++ files
        ref_builder = ReferenceBuilder(self._conn, self._symbol_name_to_id)
        rows = self._conn.execute(
            "SELECT id, path FROM files WHERE file_type IN ('header', 'source', 'inline')"
        ).fetchall()
        for row in rows:
            fpath = Path(row[1])
            try:
                ref_builder.extract_references(fpath, row[0])
            except Exception:
                logger.warning("Error extracting refs from %s", fpath, exc_info=True)
        self._conn.commit()

    # ── Private helpers ─────────────────────────────────────────────────

    def _index_cpp_file(self, path: Path, mod_id: int) -> int:
        """Parse and store a C++ file. Returns symbol count."""
        result = self._cpp_parser.parse_file(path)

        ext = path.suffix.lower()
        file_type = _EXT_TO_FILETYPE.get(ext, "source")

        file_id = insert_file(
            self._conn,
            path=str(path),
            module_id=mod_id,
            file_type=file_type,
            line_count=len(result.source_lines),
            last_modified=path.stat().st_mtime,
        )

        # Includes
        for inc_path in result.includes:
            # Determine line number — scan source_lines for the include
            line_num = 0
            for i, line in enumerate(result.source_lines, 1):
                if inc_path in line and "#include" in line:
                    line_num = i
                    break
            insert_include(
                self._conn,
                file_id=file_id,
                included_path=inc_path,
                line=line_num,
            )

        # Symbols
        count = 0
        for sym in result.symbols:
            # Skip include-kind symbols from shader parser fallback
            if sym.kind == "include":
                continue

            qualified_name = sym.name
            if sym.parent_class:
                qualified_name = f"{sym.parent_class}::{sym.name}"

            parent_symbol_id = None
            if sym.parent_class and sym.parent_class in self._symbol_name_to_id:
                parent_symbol_id = self._symbol_name_to_id[sym.parent_class]

            sym_id = insert_symbol(
                self._conn,
                name=sym.name,
                qualified_name=qualified_name,
                kind=sym.kind,
                file_id=file_id,
                line_start=sym.line_start,
                line_end=sym.line_end,
                parent_symbol_id=parent_symbol_id,
                access=sym.access or None,
                signature=sym.signature or None,
                docstring=sym.docstring or None,
                is_ue_macro=1 if sym.is_ue_macro else 0,
            )

            # Track all symbols for reference resolution — prefer definitions
            # over forward declarations (multi-line span = real definition)
            self._update_symbol_map(sym.name, sym_id, sym.line_start, sym.line_end)
            if qualified_name != sym.name:
                self._update_symbol_map(qualified_name, sym_id, sym.line_start, sym.line_end)

            # Track classes/structs separately for inheritance — prefer definitions
            if sym.kind in ("class", "struct"):
                self._update_class_map(sym.name, sym_id, sym.line_start, sym.line_end)
                if sym.base_classes:
                    # Only store bases if not already set (first definition is canonical)
                    self._symbol_name_to_id.setdefault(f"_bases_{sym.name}", sym.base_classes)

            count += 1

        # Source FTS
        self._insert_source_lines(file_id, result.source_lines)

        return count

    def _index_shader_file(self, path: Path, mod_id: int) -> int:
        """Parse and store a shader file. Returns symbol count."""
        result = self._shader_parser.parse_file(path)

        ext = path.suffix.lower()
        file_type = _EXT_TO_FILETYPE.get(ext, "shader")

        # Read lines for FTS
        try:
            source_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            source_text = ""
        source_lines = source_text.splitlines()

        file_id = insert_file(
            self._conn,
            path=str(path),
            module_id=mod_id,
            file_type=file_type,
            line_count=len(source_lines),
            last_modified=path.stat().st_mtime,
        )

        # Includes — from result.includes list
        for inc_path in result.includes:
            line_num = 0
            for i, line in enumerate(source_lines, 1):
                if inc_path in line and "#include" in line:
                    line_num = i
                    break
            insert_include(
                self._conn,
                file_id=file_id,
                included_path=inc_path,
                line=line_num,
            )

        # Symbols
        count = 0
        for sym in result.symbols:
            # Skip include-kind symbols (already tracked via includes)
            if sym.kind == "include":
                continue

            insert_symbol(
                self._conn,
                name=sym.name,
                qualified_name=sym.name,
                kind=sym.kind,
                file_id=file_id,
                line_start=sym.line_start,
                line_end=sym.line_end,
                parent_symbol_id=None,
                access=None,
                signature=sym.signature or None,
                docstring=sym.docstring or None,
                is_ue_macro=0,
            )
            count += 1

        self._insert_source_lines(file_id, source_lines)

        return count

    def _insert_source_lines(self, file_id: int, lines: list[str]) -> None:
        """Group every 10 consecutive lines into one FTS row.

        Includes blank lines to keep line numbers exact — chunk_start is the
        1-based line number of the first line in the chunk.
        """
        batch: list[tuple[int, int, str]] = []

        for i in range(0, len(lines), 10):
            chunk = lines[i : i + 10]
            chunk_start = i + 1  # 1-based
            batch.append((file_id, chunk_start, "\n".join(chunk)))

        if batch:
            self._conn.executemany(
                "INSERT INTO source_fts (file_id, line_number, text) VALUES (?, ?, ?)",
                batch,
            )

    @staticmethod
    def _is_definition(line_start: int, line_end: int) -> bool:
        """A multi-line span indicates a real definition, not a forward declaration."""
        return line_end > line_start

    def _update_symbol_map(
        self, name: str, sym_id: int, line_start: int, line_end: int
    ) -> None:
        """Update _symbol_name_to_id, preferring definitions over forward decls."""
        if name.startswith("_bases_"):
            return  # Don't interfere with base-class tracking
        existing_span = self._symbol_spans.get(name)
        if existing_span is None:
            # No existing entry — just set it
            self._symbol_name_to_id[name] = sym_id
            self._symbol_spans[name] = (line_start, line_end)
        elif self._is_definition(line_start, line_end) and not self._is_definition(*existing_span):
            # New is a definition, existing is a forward decl — overwrite
            self._symbol_name_to_id[name] = sym_id
            self._symbol_spans[name] = (line_start, line_end)
        # Otherwise keep existing (it's already a definition, or both are forward decls)

    def _update_class_map(
        self, name: str, sym_id: int, line_start: int, line_end: int
    ) -> None:
        """Update _class_name_to_id, preferring definitions over forward decls."""
        existing_span = self._class_spans.get(name)
        if existing_span is None:
            self._class_name_to_id[name] = sym_id
            self._class_spans[name] = (line_start, line_end)
        elif self._is_definition(line_start, line_end) and not self._is_definition(*existing_span):
            self._class_name_to_id[name] = sym_id
            self._class_spans[name] = (line_start, line_end)

    def _resolve_inheritance(self) -> None:
        """Second pass: resolve base class names to symbol IDs and insert inheritance.

        Uses _class_name_to_id (class/struct only) so that constructors
        and other symbols with the same name don't shadow class entries.
        """
        keys_to_process = [k for k in self._symbol_name_to_id if k.startswith("_bases_")]
        for key in keys_to_process:
            child_name = key[len("_bases_"):]
            base_classes = self._symbol_name_to_id[key]
            child_id = self._class_name_to_id.get(child_name)
            if child_id is None:
                continue
            for parent_name in base_classes:
                parent_id = self._class_name_to_id.get(parent_name)
                if parent_id is not None:
                    try:
                        insert_inheritance(
                            self._conn,
                            child_id=child_id,
                            parent_id=parent_id,
                        )
                    except sqlite3.IntegrityError:
                        pass  # Already exists
