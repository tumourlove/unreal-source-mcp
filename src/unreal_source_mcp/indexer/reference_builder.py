"""Cross-reference extraction — finds call sites and type references."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from tree_sitter import Node, Parser

from unreal_source_mcp.indexer.cpp_parser import CPP_LANGUAGE
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
        self._parser = Parser(CPP_LANGUAGE)

    def extract_references(self, path: Path, file_id: int) -> int:
        """Parse a C++ file and insert call references. Returns count."""
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
        results: list[Node] = []
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
        if fn.type == "field_expression":
            field = fn.child_by_field_name("field")
            if field:
                return field.text.decode()
            if fn.named_children:
                return fn.named_children[-1].text.decode()
        return None

    def _resolve_symbol(self, name: str | None) -> int | None:
        """Look up a symbol name in our map. Handles qualified names."""
        if name is None:
            return None
        sym_id = self._sym_map.get(name)
        if sym_id is not None:
            return sym_id
        if "::" in name:
            short = name.rsplit("::", 1)[-1]
            return self._sym_map.get(short)
        return None
