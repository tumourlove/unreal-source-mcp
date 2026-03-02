"""Tests for the C++ parser."""

from pathlib import Path

import pytest

from unreal_source_mcp.indexer.cpp_parser import CppParser, ParsedSymbol, ParseResult

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"
HEADER = FIXTURES / "SampleActor.h"
CPP = FIXTURES / "SampleActor.cpp"
ERROR_NODE_HEADER = FIXTURES / "ErrorNodeClass.h"


@pytest.fixture
def parser():
    return CppParser()


@pytest.fixture
def header_result(parser):
    return parser.parse_file(HEADER)


@pytest.fixture
def cpp_result(parser):
    return parser.parse_file(CPP)


@pytest.fixture
def error_node_result(parser):
    return parser.parse_file(ERROR_NODE_HEADER)


# ── Helper ──────────────────────────────────────────────────────────
def _find(result: ParseResult, name: str, kind: str | None = None) -> ParsedSymbol | None:
    for s in result.symbols:
        if s.name == name and (kind is None or s.kind == kind):
            return s
    return None


def _find_all(result: ParseResult, kind: str) -> list[ParsedSymbol]:
    return [s for s in result.symbols if s.kind == kind]


# ── Includes ────────────────────────────────────────────────────────
class TestIncludes:
    def test_header_includes(self, header_result):
        assert "CoreMinimal.h" in header_result.includes
        assert "GameFramework/Actor.h" in header_result.includes
        assert "SampleActor.generated.h" in header_result.includes

    def test_cpp_includes(self, cpp_result):
        assert "SampleActor.h" in cpp_result.includes
        assert "Engine/World.h" in cpp_result.includes


# ── Class extraction ────────────────────────────────────────────────
class TestClassExtraction:
    def test_finds_class(self, header_result):
        cls = _find(header_result, "ASampleActor", "class")
        assert cls is not None

    def test_base_class(self, header_result):
        cls = _find(header_result, "ASampleActor", "class")
        assert "AActor" in cls.base_classes

    def test_class_ue_macro(self, header_result):
        cls = _find(header_result, "ASampleActor", "class")
        assert cls.is_ue_macro is True

    def test_class_docstring(self, header_result):
        cls = _find(header_result, "ASampleActor", "class")
        assert "sample actor" in cls.docstring.lower()


# ── Enum extraction ─────────────────────────────────────────────────
class TestEnumExtraction:
    def test_finds_enum(self, header_result):
        en = _find(header_result, "ESampleState", "enum")
        assert en is not None

    def test_enum_ue_macro(self, header_result):
        en = _find(header_result, "ESampleState", "enum")
        assert en.is_ue_macro is True


# ── Struct extraction ───────────────────────────────────────────────
class TestStructExtraction:
    def test_finds_struct(self, header_result):
        st = _find(header_result, "FSampleData", "struct")
        assert st is not None

    def test_struct_ue_macro(self, header_result):
        st = _find(header_result, "FSampleData", "struct")
        assert st.is_ue_macro is True

    def test_struct_members(self, header_result):
        value = _find(header_result, "Value", "variable")
        label = _find(header_result, "Label", "variable")
        assert value is not None
        assert label is not None
        assert value.parent_class == "FSampleData"
        assert label.parent_class == "FSampleData"


# ── Function declarations (header) ─────────────────────────────────
class TestFunctionDeclarations:
    def test_finds_dosomething(self, header_result):
        fn = _find(header_result, "DoSomething", "function")
        assert fn is not None

    def test_finds_gethealth(self, header_result):
        fn = _find(header_result, "GetHealth", "function")
        assert fn is not None

    def test_finds_internalhelper(self, header_result):
        fn = _find(header_result, "InternalHelper", "function")
        assert fn is not None

    def test_dosomething_signature(self, header_result):
        fn = _find(header_result, "DoSomething", "function")
        assert "float DeltaTime" in fn.signature

    def test_gethealth_signature(self, header_result):
        fn = _find(header_result, "GetHealth", "function")
        assert "const" in fn.signature

    def test_dosomething_ue_macro(self, header_result):
        fn = _find(header_result, "DoSomething", "function")
        assert fn.is_ue_macro is True

    def test_gethealth_ue_macro(self, header_result):
        fn = _find(header_result, "GetHealth", "function")
        assert fn.is_ue_macro is True

    def test_internalhelper_not_ue_macro(self, header_result):
        fn = _find(header_result, "InternalHelper", "function")
        assert fn.is_ue_macro is False

    def test_dosomething_docstring(self, header_result):
        fn = _find(header_result, "DoSomething", "function")
        assert "every frame" in fn.docstring.lower()

    def test_gethealth_docstring(self, header_result):
        fn = _find(header_result, "GetHealth", "function")
        assert "health" in fn.docstring.lower()


# ── Properties (variables) ──────────────────────────────────────────
class TestProperties:
    def test_finds_health(self, header_result):
        v = _find(header_result, "Health", "variable")
        assert v is not None

    def test_finds_tickcount(self, header_result):
        v = _find(header_result, "TickCount", "variable")
        assert v is not None

    def test_health_ue_macro(self, header_result):
        v = _find(header_result, "Health", "variable")
        assert v.is_ue_macro is True

    def test_tickcount_not_ue_macro(self, header_result):
        v = _find(header_result, "TickCount", "variable")
        assert v.is_ue_macro is False

    def test_health_docstring(self, header_result):
        v = _find(header_result, "Health", "variable")
        assert "health" in v.docstring.lower()

    def test_tickcount_docstring(self, header_result):
        v = _find(header_result, "TickCount", "variable")
        assert "tick counter" in v.docstring.lower()


# ── Access specifiers ───────────────────────────────────────────────
class TestAccessSpecifiers:
    def test_dosomething_public(self, header_result):
        fn = _find(header_result, "DoSomething", "function")
        assert fn.access == "public"

    def test_gethealth_public(self, header_result):
        fn = _find(header_result, "GetHealth", "function")
        assert fn.access == "public"

    def test_health_protected(self, header_result):
        v = _find(header_result, "Health", "variable")
        assert v.access == "protected"

    def test_tickcount_private(self, header_result):
        v = _find(header_result, "TickCount", "variable")
        assert v.access == "private"

    def test_internalhelper_private(self, header_result):
        fn = _find(header_result, "InternalHelper", "function")
        assert fn.access == "private"


# ── .cpp function definitions ───────────────────────────────────────
class TestCppDefinitions:
    def test_finds_constructor(self, cpp_result):
        fn = _find(cpp_result, "ASampleActor::ASampleActor", "function")
        assert fn is not None

    def test_constructor_parent_class(self, cpp_result):
        fn = _find(cpp_result, "ASampleActor::ASampleActor", "function")
        assert fn.parent_class == "ASampleActor"

    def test_finds_dosomething_def(self, cpp_result):
        fn = _find(cpp_result, "ASampleActor::DoSomething", "function")
        assert fn is not None

    def test_dosomething_signature(self, cpp_result):
        fn = _find(cpp_result, "ASampleActor::DoSomething", "function")
        assert "float DeltaTime" in fn.signature

    def test_finds_gethealth_def(self, cpp_result):
        fn = _find(cpp_result, "ASampleActor::GetHealth", "function")
        assert fn is not None

    def test_gethealth_const(self, cpp_result):
        fn = _find(cpp_result, "ASampleActor::GetHealth", "function")
        assert "const" in fn.signature

    def test_finds_internalhelper_def(self, cpp_result):
        fn = _find(cpp_result, "ASampleActor::InternalHelper", "function")
        assert fn is not None

    def test_all_methods_have_parent(self, cpp_result):
        fns = _find_all(cpp_result, "function")
        assert len(fns) == 5  # constructor + 3 methods + 1 free function
        methods = [fn for fn in fns if fn.parent_class is not None]
        assert len(methods) == 4
        for fn in methods:
            assert fn.parent_class == "ASampleActor"


# ── GENERATED_BODY not extracted ────────────────────────────────────
class TestGeneratedBody:
    def test_no_generated_body_symbol(self, header_result):
        gb = _find(header_result, "GENERATED_BODY")
        assert gb is None


# ── Source lines ────────────────────────────────────────────────────
class TestSourceLines:
    def test_source_lines_populated(self, header_result):
        assert len(header_result.source_lines) > 0

    def test_path_set(self, header_result):
        assert "SampleActor.h" in header_result.path


# ── ERROR / misparsed node recovery ─────────────────────────────────
class TestErrorNodeRecovery:
    def test_error_node_class_extracted(self, error_node_result):
        cls = _find(error_node_result, "UMultiInterfaceComponent", "class")
        assert cls is not None

    def test_error_node_base_classes(self, error_node_result):
        cls = _find(error_node_result, "UMultiInterfaceComponent", "class")
        assert "UActorComponent" in cls.base_classes
        assert "IInterface1" in cls.base_classes
        assert "IInterface2" in cls.base_classes

    def test_error_node_ue_macro(self, error_node_result):
        cls = _find(error_node_result, "UMultiInterfaceComponent", "class")
        assert cls.is_ue_macro is True

    def test_error_node_members(self, error_node_result):
        fn = _find(error_node_result, "DoMultiThing", "function")
        assert fn is not None
        assert fn.parent_class == "UMultiInterfaceComponent"
        assert fn.is_ue_macro is True
        assert fn.access == "public"

        var = _find(error_node_result, "Speed", "variable")
        assert var is not None
        assert var.parent_class == "UMultiInterfaceComponent"
        assert var.is_ue_macro is True
        assert var.access == "public"
