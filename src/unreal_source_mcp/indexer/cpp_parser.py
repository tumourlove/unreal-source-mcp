"""C++ parser using tree-sitter for Unreal Engine source files.

Extracts classes, structs, enums, functions, variables, and UE macro metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_cpp as tscpp
from tree_sitter import Language, Parser

CPP_LANGUAGE = Language(tscpp.language())

UE_MACROS = {"UCLASS", "USTRUCT", "UENUM", "UFUNCTION", "UPROPERTY", "UINTERFACE"}


@dataclass
class ParsedSymbol:
    """A single extracted symbol from a C++ file."""

    name: str
    kind: str  # class, struct, function, enum, variable, macro, typedef
    line_start: int
    line_end: int
    signature: str = ""
    docstring: str = ""
    access: str = ""  # public, protected, private
    is_ue_macro: bool = False
    base_classes: list[str] = field(default_factory=list)
    parent_class: str | None = None


@dataclass
class ParseResult:
    """Result of parsing a single C++ file."""

    path: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)


class CppParser:
    """Parses C++ source files using tree-sitter and extracts symbols."""

    def __init__(self) -> None:
        self._parser = Parser(CPP_LANGUAGE)

    def parse_file(self, path: str | Path) -> ParseResult:
        """Parse a C++ file and return extracted symbols."""
        path = Path(path)
        source_bytes = path.read_bytes()
        source_text = source_bytes.decode("utf-8", errors="replace")
        source_lines = source_text.splitlines()

        tree = self._parser.parse(source_bytes)
        root = tree.root_node

        result = ParseResult(
            path=str(path),
            source_lines=source_lines,
        )

        # Extract includes
        result.includes = self._extract_includes(root)

        # Extract symbols from the top-level translation unit
        self._extract_symbols(root, source_lines, result)

        return result

    # ------------------------------------------------------------------
    # Include extraction
    # ------------------------------------------------------------------

    def _extract_includes(self, root) -> list[str]:
        """Extract all #include paths."""
        includes: list[str] = []
        for node in root.children:
            if node.type == "preproc_include":
                # Find the string_literal or system_lib_string child
                for child in node.children:
                    if child.type == "string_literal":
                        # Strip quotes
                        path_text = child.text.decode()
                        path_text = path_text.strip('"')
                        includes.append(path_text)
                    elif child.type == "system_lib_string":
                        path_text = child.text.decode()
                        path_text = path_text.strip("<>")
                        includes.append(path_text)
        return includes

    # ------------------------------------------------------------------
    # Symbol extraction — top-level dispatch
    # ------------------------------------------------------------------

    def _extract_symbols(self, root, source_lines: list[str], result: ParseResult) -> None:
        """Walk top-level children and extract symbols."""
        children = list(root.children)
        i = 0
        while i < len(children):
            node = children[i]

            # Check for UE macro preceding a declaration
            ue_macro = self._try_get_ue_macro(node)

            if ue_macro and i + 1 < len(children):
                next_node = children[i + 1]
                # UE macro precedes the next declaration
                if next_node.type in ("class_specifier", "struct_specifier", "enum_specifier"):
                    self._extract_class_or_struct_or_enum(
                        next_node, source_lines, result, ue_macro=ue_macro
                    )
                    i += 2
                    continue
                elif next_node.type == "function_definition":
                    # In UE headers, UCLASS() before a class that tree-sitter
                    # misparses as a function_definition (due to ENGINE_API)
                    self._extract_misparse_class_or_function(
                        next_node, source_lines, result, ue_macro=ue_macro
                    )
                    i += 2
                    continue

            if node.type in ("class_specifier", "struct_specifier"):
                self._extract_class_or_struct_or_enum(node, source_lines, result)
                i += 1
                continue

            if node.type == "enum_specifier":
                self._extract_class_or_struct_or_enum(node, source_lines, result)
                i += 1
                continue

            if node.type == "function_definition":
                self._extract_misparse_class_or_function(node, source_lines, result)
                i += 1
                continue

            i += 1

    # ------------------------------------------------------------------
    # UE macro detection
    # ------------------------------------------------------------------

    def _try_get_ue_macro(self, node) -> str | None:
        """If this node is a UE macro call (expression_statement wrapping
        a call_expression like UCLASS(...)), return the macro name."""
        if node.type == "expression_statement":
            for child in node.children:
                if child.type == "call_expression":
                    fn = child.child_by_field_name("function")
                    if fn is None and child.children:
                        fn = child.children[0]
                    if fn and fn.type == "identifier":
                        name = fn.text.decode()
                        if name in UE_MACROS:
                            return name
        return None

    # ------------------------------------------------------------------
    # Class / struct / enum extraction
    # ------------------------------------------------------------------

    def _extract_class_or_struct_or_enum(
        self, node, source_lines: list[str], result: ParseResult, ue_macro: str | None = None
    ) -> None:
        """Extract a class_specifier, struct_specifier, or enum_specifier."""
        kind_map = {
            "class_specifier": "class",
            "struct_specifier": "struct",
            "enum_specifier": "enum",
        }
        kind = kind_map.get(node.type, "class")

        name = self._get_type_name(node)
        if not name:
            return

        base_classes = self._get_base_classes(node)
        docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)
        signature = node.text.decode().split("{")[0].strip() if node.text else ""

        symbol = ParsedSymbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=signature,
            docstring=docstring,
            is_ue_macro=ue_macro is not None,
            base_classes=base_classes,
        )
        result.symbols.append(symbol)

        # For class/struct, extract members from field_declaration_list
        if kind in ("class", "struct"):
            body = None
            for child in node.children:
                if child.type == "field_declaration_list":
                    body = child
                    break
            if body:
                self._extract_members_from_field_list(
                    body, source_lines, result, parent_class=name, default_access="private" if kind == "class" else "public"
                )

    def _get_type_name(self, node) -> str | None:
        """Get the name of a class/struct/enum from tree-sitter node."""
        # Look for type_identifier child
        for child in node.children:
            if child.type == "type_identifier":
                return child.text.decode()
        # Also check named children
        name_node = node.child_by_field_name("name")
        if name_node:
            return name_node.text.decode()
        return None

    def _get_base_classes(self, node) -> list[str]:
        """Extract base classes from a base_class_clause."""
        bases: list[str] = []
        for child in node.children:
            if child.type == "base_class_clause":
                for bc_child in child.children:
                    if bc_child.type == "type_identifier":
                        bases.append(bc_child.text.decode())
        return bases

    # ------------------------------------------------------------------
    # Members from field_declaration_list (struct bodies)
    # ------------------------------------------------------------------

    def _extract_members_from_field_list(
        self, body_node, source_lines: list[str], result: ParseResult,
        parent_class: str = "", default_access: str = "private"
    ) -> None:
        """Extract members from a field_declaration_list (used by properly parsed structs/classes)."""
        current_access = default_access
        children = list(body_node.children)
        i = 0

        while i < len(children):
            child = children[i]

            if child.type == "access_specifier":
                current_access = self._get_access(child)
                i += 1
                continue

            # Check for UE macro (UPROPERTY/UFUNCTION parsed as field_declaration with type UPROPERTY)
            ue_macro = self._try_get_ue_macro_field(child)
            if ue_macro:
                # The actual member follows
                if i + 1 < len(children):
                    next_child = children[i + 1]
                    self._extract_field_or_func_decl(
                        next_child, source_lines, result,
                        parent_class=parent_class, access=current_access, ue_macro=ue_macro
                    )
                    i += 2
                    continue
                i += 1
                continue

            # Skip GENERATED_BODY() and braces
            if child.type in ("{", "}"):
                i += 1
                continue
            if child.type == "declaration" and "GENERATED_BODY" in (child.text.decode() if child.text else ""):
                i += 1
                continue

            # Regular field/function declaration
            self._extract_field_or_func_decl(
                child, source_lines, result,
                parent_class=parent_class, access=current_access
            )
            i += 1

    def _try_get_ue_macro_field(self, node) -> str | None:
        """Detect UPROPERTY/UFUNCTION etc. in a field_declaration_list context.
        These appear as field_declaration with type_identifier=UPROPERTY."""
        if node.type == "field_declaration":
            for child in node.children:
                if child.type == "type_identifier" and child.text.decode() in UE_MACROS:
                    return child.text.decode()
        # Also check expression_statement (sometimes macros appear this way)
        return self._try_get_ue_macro(node)

    def _extract_field_or_func_decl(
        self, node, source_lines: list[str], result: ParseResult,
        parent_class: str = "", access: str = "", ue_macro: str | None = None
    ) -> None:
        """Extract a single field_declaration as either a variable or function declaration."""
        if node.type not in ("field_declaration", "declaration", "function_definition"):
            return

        text = node.text.decode() if node.text else ""
        # Skip GENERATED_BODY
        if "GENERATED_BODY" in text:
            return
        # Skip UE macro-only lines (e.g. UPROPERTY(EditAnywhere) with no actual field)
        first_type = None
        for child in node.children:
            if child.type == "type_identifier":
                first_type = child.text.decode()
                break

        if first_type in UE_MACROS:
            return

        # Determine if this is a function or variable
        has_func_declarator = any(c.type == "function_declarator" for c in node.children)

        if has_func_declarator:
            name = self._get_func_declarator_name(node)
            if not name:
                return
            docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)
            sig = text.rstrip(";").strip()
            result.symbols.append(ParsedSymbol(
                name=name,
                kind="function",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=sig,
                docstring=docstring,
                access=access,
                is_ue_macro=ue_macro is not None,
                parent_class=parent_class,
            ))
        else:
            name = self._get_field_name(node)
            if not name:
                return
            docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)
            sig = text.rstrip(";").strip()
            result.symbols.append(ParsedSymbol(
                name=name,
                kind="variable",
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=sig,
                docstring=docstring,
                access=access,
                is_ue_macro=ue_macro is not None,
                parent_class=parent_class,
            ))

    def _get_func_declarator_name(self, node) -> str | None:
        """Get function name from a node containing a function_declarator."""
        for child in node.children:
            if child.type == "function_declarator":
                # Look for identifier or qualified_identifier
                for fc in child.children:
                    if fc.type == "identifier":
                        return fc.text.decode()
                    if fc.type == "field_identifier":
                        return fc.text.decode()
                    if fc.type == "qualified_identifier":
                        return fc.text.decode()
                # Fallback: the destructor_name or first named child
                if child.named_children:
                    return child.named_children[0].text.decode()
        return None

    def _get_field_name(self, node) -> str | None:
        """Get variable name from a field_declaration or declaration."""
        for child in node.children:
            if child.type == "field_identifier":
                return child.text.decode()
            if child.type == "identifier":
                # Skip type identifiers that come before the field name
                # The last identifier is typically the variable name
                pass
        # For declarations like "int32 TickCount;", look for identifier after type
        identifiers = [c for c in node.children if c.type == "identifier"]
        if identifiers:
            return identifiers[-1].text.decode()
        # Check for field_identifier
        for child in node.children:
            if child.type == "field_identifier":
                return child.text.decode()
        return None

    # ------------------------------------------------------------------
    # Misparsed class (tree-sitter sees it as function_definition)
    # ------------------------------------------------------------------

    def _extract_misparse_class_or_function(
        self, node, source_lines: list[str], result: ParseResult, ue_macro: str | None = None
    ) -> None:
        """Handle a function_definition that may actually be a misparsed class
        (due to ENGINE_API or similar export macros) or a real function."""
        # Check if this is actually a class (has class_specifier child)
        has_class_spec = any(c.type == "class_specifier" for c in node.children)
        has_struct_spec = any(c.type == "struct_specifier" for c in node.children)

        if has_class_spec or has_struct_spec:
            self._extract_misparsed_class(node, source_lines, result, ue_macro=ue_macro)
        else:
            self._extract_function_definition(node, source_lines, result, ue_macro=ue_macro)

    def _extract_misparsed_class(
        self, node, source_lines: list[str], result: ParseResult, ue_macro: str | None = None
    ) -> None:
        """Extract a class that tree-sitter misparsed as function_definition
        due to ENGINE_API macro. Structure:
          function_definition -> class_specifier(ENGINE_API) + identifier(ClassName) + ERROR(:public Base) + compound_statement({body})
        """
        kind = "class"
        for child in node.children:
            if child.type == "struct_specifier":
                kind = "struct"
                break

        # Get the class name (the identifier child)
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = child.text.decode()
                break

        if not name:
            return

        # Get base classes from ERROR node containing ": public BaseClass"
        base_classes: list[str] = []
        for child in node.children:
            if child.type == "ERROR":
                for ec in child.children:
                    if ec.type == "identifier":
                        base_classes.append(ec.text.decode())

        docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)

        # Build signature from first line(s) up to {
        sig_text = node.text.decode().split("{")[0].strip() if node.text else ""

        symbol = ParsedSymbol(
            name=name,
            kind=kind,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=sig_text,
            docstring=docstring,
            is_ue_macro=ue_macro is not None,
            base_classes=base_classes,
        )
        result.symbols.append(symbol)

        # Extract members from compound_statement body
        body = None
        for child in node.children:
            if child.type == "compound_statement":
                body = child
                break

        if body:
            self._extract_members_from_compound(
                body, source_lines, result, parent_class=name, default_access="private" if kind == "class" else "public"
            )

    # ------------------------------------------------------------------
    # Members from compound_statement (misparsed class bodies)
    # ------------------------------------------------------------------

    def _extract_members_from_compound(
        self, body_node, source_lines: list[str], result: ParseResult,
        parent_class: str = "", default_access: str = "private"
    ) -> None:
        """Extract members from a compound_statement (misparsed class body).
        Access specifiers appear as labeled_statement nodes."""
        current_access = default_access
        children = list(body_node.children)
        i = 0

        while i < len(children):
            child = children[i]

            if child.type in ("{", "}"):
                i += 1
                continue

            # labeled_statement = access specifier region
            if child.type == "labeled_statement":
                current_access, pending_macro = self._extract_from_labeled(
                    child, source_lines, result, parent_class, current_access
                )
                # If the labeled_statement ended with a UE macro that had no
                # declaration inside it, the actual declaration is the next sibling.
                if pending_macro and i + 1 < len(children):
                    next_child = children[i + 1]
                    self._extract_compound_member(
                        next_child, source_lines, result,
                        parent_class=parent_class, access=current_access, ue_macro=pending_macro
                    )
                    i += 2
                    continue
                i += 1
                continue

            # UE macro (expression_statement containing call to UFUNCTION/UPROPERTY etc.)
            ue_macro = self._try_get_ue_macro(child)
            if ue_macro:
                if i + 1 < len(children):
                    next_child = children[i + 1]
                    self._extract_compound_member(
                        next_child, source_lines, result,
                        parent_class=parent_class, access=current_access, ue_macro=ue_macro
                    )
                    i += 2
                    continue
                i += 1
                continue

            # Skip GENERATED_BODY
            if child.type == "expression_statement":
                text = child.text.decode() if child.text else ""
                if "GENERATED_BODY" in text:
                    i += 1
                    continue

            # Regular member
            self._extract_compound_member(
                child, source_lines, result,
                parent_class=parent_class, access=current_access
            )
            i += 1

    def _extract_from_labeled(
        self, node, source_lines: list[str], result: ParseResult,
        parent_class: str, current_access: str
    ) -> tuple[str, str | None]:
        """Extract members from a labeled_statement (access: declarations...).
        Returns (new_access, pending_ue_macro). pending_ue_macro is set when
        the last child was a UE macro with no following declaration inside
        the labeled_statement — the caller must apply it to the next sibling."""
        # The label is the access specifier
        for child in node.children:
            if child.type == "statement_identifier":
                label = child.text.decode()
                if label in ("public", "protected", "private"):
                    current_access = label

        # Process children of the labeled_statement (the declarations within this access region)
        children = list(node.children)
        i = 0
        pending_macro: str | None = None
        while i < len(children):
            child = children[i]
            if child.type in ("statement_identifier", ":"):
                i += 1
                continue

            # Comments may be docstrings for the next sibling
            if child.type == "comment":
                i += 1
                continue

            # UE macro
            ue_macro = self._try_get_ue_macro(child)
            if ue_macro:
                if i + 1 < len(children):
                    next_child = children[i + 1]
                    # Check if the next child is an actual declaration or just a comment
                    if next_child.type == "comment":
                        # Macro followed by comment — the declaration is further out
                        pending_macro = ue_macro
                        i += 1
                        continue
                    self._extract_compound_member(
                        next_child, source_lines, result,
                        parent_class=parent_class, access=current_access, ue_macro=ue_macro
                    )
                    pending_macro = None
                    i += 2
                    continue
                else:
                    # UE macro is the last child — declaration is next sibling of labeled_statement
                    pending_macro = ue_macro
                i += 1
                continue

            self._extract_compound_member(
                child, source_lines, result,
                parent_class=parent_class, access=current_access
            )
            pending_macro = None
            i += 1

        return current_access, pending_macro

    def _extract_compound_member(
        self, node, source_lines: list[str], result: ParseResult,
        parent_class: str = "", access: str = "", ue_macro: str | None = None
    ) -> None:
        """Extract a member from inside a compound_statement (misparsed class body).
        These are typically declaration or expression_statement nodes."""
        if node.type == "comment":
            return

        text = node.text.decode() if node.text else ""

        # Skip GENERATED_BODY
        if "GENERATED_BODY" in text:
            return

        if node.type == "declaration":
            has_func_declarator = any(c.type == "function_declarator" for c in node.children)
            if has_func_declarator:
                name = self._get_func_declarator_name(node)
                if name:
                    docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)
                    sig = text.rstrip(";").strip()
                    result.symbols.append(ParsedSymbol(
                        name=name,
                        kind="function",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=sig,
                        docstring=docstring,
                        access=access,
                        is_ue_macro=ue_macro is not None,
                        parent_class=parent_class,
                    ))
            else:
                name = self._get_field_name(node)
                if name:
                    docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)
                    sig = text.rstrip(";").strip()
                    result.symbols.append(ParsedSymbol(
                        name=name,
                        kind="variable",
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        signature=sig,
                        docstring=docstring,
                        access=access,
                        is_ue_macro=ue_macro is not None,
                        parent_class=parent_class,
                    ))
        elif node.type == "expression_statement":
            # Could be a constructor call like ASampleActor();
            for child in node.children:
                if child.type == "call_expression":
                    fn = child.children[0] if child.children else None
                    if fn and fn.type == "identifier":
                        fn_name = fn.text.decode()
                        if fn_name not in UE_MACROS and fn_name != "GENERATED_BODY":
                            docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)
                            sig = text.rstrip(";").strip()
                            result.symbols.append(ParsedSymbol(
                                name=fn_name,
                                kind="function",
                                line_start=node.start_point[0] + 1,
                                line_end=node.end_point[0] + 1,
                                signature=sig,
                                docstring=docstring,
                                access=access,
                                is_ue_macro=ue_macro is not None,
                                parent_class=parent_class,
                            ))

    # ------------------------------------------------------------------
    # Function definitions (.cpp or free functions)
    # ------------------------------------------------------------------

    def _extract_function_definition(
        self, node, source_lines: list[str], result: ParseResult, ue_macro: str | None = None
    ) -> None:
        """Extract a proper function_definition (in .cpp files or free functions)."""
        func_decl = None
        for child in node.children:
            if child.type == "function_declarator":
                func_decl = child
                break

        if not func_decl:
            return

        # Get function name — may be qualified (Class::Method)
        name = None
        parent_class = None
        for child in func_decl.children:
            if child.type == "qualified_identifier":
                qname = child.text.decode()
                name = qname
                if "::" in qname:
                    parts = qname.split("::")
                    parent_class = parts[0]
                break
            if child.type == "identifier":
                name = child.text.decode()
                break

        if not name:
            return

        docstring = self._get_docstring_above(node, source_lines, ue_macro_above=ue_macro is not None)

        # Build signature from everything before the compound_statement
        sig_parts = []
        for child in node.children:
            if child.type == "compound_statement":
                break
            sig_parts.append(child.text.decode() if child.text else "")
        signature = " ".join(sig_parts).strip()

        result.symbols.append(ParsedSymbol(
            name=name,
            kind="function",
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=signature,
            docstring=docstring,
            is_ue_macro=ue_macro is not None,
            parent_class=parent_class,
        ))

    # ------------------------------------------------------------------
    # Docstring extraction
    # ------------------------------------------------------------------

    def _get_docstring_above(
        self, node, source_lines: list[str], ue_macro_above: bool = False
    ) -> str:
        """Extract a doc comment (/** */ or ///) immediately above a node.
        If a UE macro is above the node, look above that too.
        Uses pure line-based search to handle tree-sitter AST quirks."""
        target_line = node.start_point[0]  # 0-indexed

        # Walk backwards from the line above the node
        search_line = target_line - 1

        if ue_macro_above:
            # Skip upward past the UE macro line(s) and any blank lines
            while search_line >= 0:
                line = source_lines[search_line].strip()
                if not line or self._is_ue_macro_line(line):
                    search_line -= 1
                else:
                    break
        else:
            # Skip blank lines
            while search_line >= 0 and not source_lines[search_line].strip():
                search_line -= 1

        # Collect comment lines going upward
        doc_lines: list[str] = []
        line_idx = search_line
        while line_idx >= 0:
            line = source_lines[line_idx].strip()
            if line.startswith("///") or line.startswith("/**") or line.startswith("*") or line.startswith("*/"):
                doc_lines.insert(0, line)
                line_idx -= 1
            else:
                break

        if not doc_lines:
            return ""

        return self._clean_docstring(doc_lines)

    @staticmethod
    def _is_ue_macro_line(line: str) -> bool:
        """Check if a source line is a UE macro invocation."""
        stripped = line.strip()
        for macro in UE_MACROS:
            if stripped.startswith(macro + "(") or stripped == macro:
                return True
        return False

    def _clean_docstring(self, lines: list[str]) -> str:
        """Clean up doc comment lines into a plain text docstring."""
        cleaned: list[str] = []
        for line in lines:
            line = line.strip()
            # Remove comment markers
            if line.startswith("/**"):
                line = line[3:].strip()
            elif line.startswith("///"):
                line = line[3:].strip()
            elif line.startswith("*/"):
                continue
            elif line.startswith("*"):
                line = line[1:].strip()
            if line:
                cleaned.append(line)
        return "\n".join(cleaned)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_access(self, node) -> str:
        """Get access level from an access_specifier node."""
        text = node.text.decode().rstrip(":").strip()
        if text in ("public", "protected", "private"):
            return text
        return ""
