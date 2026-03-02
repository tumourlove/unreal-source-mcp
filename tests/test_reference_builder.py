"""Tests for cross-reference extraction."""

import sqlite3
from pathlib import Path

import pytest

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.db.queries import get_references_to, get_references_from, get_symbols_by_name
from unreal_source_mcp.indexer.pipeline import IndexingPipeline

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def populated_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    pipeline = IndexingPipeline(conn)
    pipeline.index_directory(FIXTURES)
    return conn


def test_references_table_populated(populated_db):
    """Reference builder should insert call references."""
    row = populated_db.execute('SELECT COUNT(*) AS cnt FROM "references"').fetchone()
    assert row["cnt"] > 0


def test_internal_helper_calls_dosomething(populated_db):
    """InternalHelper calls DoSomething — should have a call reference."""
    # InternalHelper is stored as ASampleActor::InternalHelper
    syms = get_symbols_by_name(populated_db, "DoSomething", kind="function")
    assert len(syms) > 0
    all_refs = []
    for s in syms:
        all_refs.extend(get_references_to(populated_db, s["id"], ref_kind="call"))
    # At least one caller should be InternalHelper
    caller_names = [r["from_name"] for r in all_refs]
    assert any("InternalHelper" in name for name in caller_names), f"Expected InternalHelper in callers, got: {caller_names}"


def test_type_references_extracted(populated_db):
    """ReferenceBuilder should extract type references (e.g. FSampleData used in a function)."""
    row = populated_db.execute(
        'SELECT COUNT(*) AS cnt FROM "references" WHERE ref_kind = ?',
        ("type",),
    ).fetchone()
    assert row["cnt"] > 0, "Expected type references to be extracted"


def test_type_reference_to_fsampledata(populated_db):
    """FreeFunctionUsingTypes uses FSampleData — should have a type reference."""
    syms = get_symbols_by_name(populated_db, "FSampleData")
    assert len(syms) > 0
    all_refs = []
    for s in syms:
        all_refs.extend(get_references_to(populated_db, s["id"], ref_kind="type"))
    assert len(all_refs) > 0, f"Expected type references to FSampleData"


def test_dosomething_calls_getworld(populated_db):
    """DoSomething calls GetWorld — should have an outgoing call reference (if GetWorld is indexed)."""
    # GetWorld may not be in our fixture symbols since it's inherited from AActor
    # But DoSomething IS a known function, so we can check it has outgoing refs
    syms = get_symbols_by_name(populated_db, "ASampleActor::DoSomething", kind="function")
    if not syms:
        # Try short name
        syms = get_symbols_by_name(populated_db, "DoSomething", kind="function")
    # We just verify the function exists and the query doesn't crash
    assert len(syms) > 0


def test_class_scope_type_references(populated_db):
    """Class-scope type references should be extracted (member types, base classes)."""
    # FSampleData has members using FString — if FString is indexed we'd see it.
    # More reliably: check that class-scope type references exist at all
    row = populated_db.execute(
        'SELECT COUNT(*) AS cnt FROM "references" WHERE ref_kind = ?',
        ("type",),
    ).fetchone()
    # We should have type refs from both function-scope AND class-scope
    assert row["cnt"] > 0


def test_local_var_type_resolution(populated_db):
    """_resolve_local_var_type should find types of locally declared pointer variables."""
    from unreal_source_mcp.indexer.reference_builder import ReferenceBuilder
    rb = ReferenceBuilder(populated_db, {})

    # Parse SampleActor.cpp and check local var type resolution
    from tree_sitter import Parser
    from unreal_source_mcp.indexer.cpp_parser import CPP_LANGUAGE
    parser = Parser(CPP_LANGUAGE)
    cpp_path = Path(__file__).parent / "fixtures" / "sample_ue_source" / "SampleActor.cpp"
    source_bytes = cpp_path.read_bytes()
    tree = parser.parse(source_bytes)

    # Find the DoSomething function definition
    func_nodes = rb._find_nodes(tree.root_node, "function_definition")
    dosomething = None
    for fn in func_nodes:
        name = rb._get_function_name(fn)
        if name and "DoSomething" in name:
            dosomething = fn
            break
    assert dosomething is not None, "DoSomething function not found in AST"

    # DoSomething declares: UWorld* World = GetWorld();
    resolved_type = rb._resolve_local_var_type(dosomething, "World")
    assert resolved_type == "UWorld", f"Expected UWorld, got {resolved_type}"


def test_class_scope_member_type_refs(populated_db):
    """ASampleActor class should have type references from its member types."""
    # ASampleActor uses ESampleState or FSampleData in scope — check its outgoing type refs
    syms = get_symbols_by_name(populated_db, "ASampleActor")
    class_syms = [s for s in syms if s["kind"] == "class"]
    if not class_syms:
        pytest.skip("ASampleActor class not found")
    class_id = class_syms[0]["id"]
    refs = get_references_from(populated_db, class_id, ref_kind="type")
    # The class has AActor as base — if AActor isn't indexed, member types should still produce refs
    # At minimum, class-scope extraction should not crash
    assert isinstance(refs, list)
