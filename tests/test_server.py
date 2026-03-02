"""Tests for the MCP server tools."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from unreal_source_mcp.db.schema import init_db
from unreal_source_mcp.indexer.pipeline import IndexingPipeline
from unreal_source_mcp import server

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"


@pytest.fixture
def populated_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    pipeline = IndexingPipeline(conn)
    pipeline.index_directory(FIXTURES)
    return conn


@pytest.fixture(autouse=True)
def mock_conn(populated_db):
    with patch.object(server, "_conn", populated_db):
        with patch.object(server, "_get_conn", return_value=populated_db):
            yield


# ── search_source ────────────────────────────────────────────────────────

def test_search_source_finds_class():
    result = server.search_source("ASampleActor")
    assert "ASampleActor" in result


def test_search_source_finds_shader():
    result = server.search_source("EncodeNormal")
    assert "EncodeNormal" in result


def test_search_source_scope_filter():
    result = server.search_source("EncodeNormal", scope="shaders")
    assert "EncodeNormal" in result


# ── read_source ──────────────────────────────────────────────────────────

def test_read_source_finds_class():
    result = server.read_source("ASampleActor")
    assert "ASampleActor" in result


def test_read_source_filters_forward_declarations():
    """read_source should not return forward declarations when full definition exists."""
    result = server.read_source("FSampleData")
    # Should contain the real definition (multi-line struct)
    assert "UPROPERTY" in result or "Value" in result
    # Should NOT contain the single-line forward declaration
    lines = result.split("\n")
    forward_decl_lines = [l for l in lines if l.strip() == "struct FSampleData;"]
    assert len(forward_decl_lines) == 0, f"Forward declarations should be filtered out, found: {forward_decl_lines}"


def test_read_source_keeps_forward_decl_when_no_definition():
    """If only forward declarations exist (no full definition), keep one."""
    result = server.read_source("UPhysicsVolume")
    # UPhysicsVolume only exists as a forward declaration, so it should still appear
    assert "UPhysicsVolume" in result


def test_read_source_max_lines():
    """read_source with max_lines should truncate output."""
    full = server.read_source("ASampleActor")
    truncated = server.read_source("ASampleActor", max_lines=5)
    # Truncated should be shorter
    assert len(truncated.split("\n")) < len(full.split("\n"))
    assert "truncated" in truncated.lower()


def test_read_source_members_only():
    """read_source with members_only should show only member declarations."""
    result = server.read_source("ASampleActor", members_only=True)
    assert "DoSomething" in result
    assert "Health" in result


# ── get_class_hierarchy ──────────────────────────────────────────────────

def test_get_class_hierarchy():
    result = server.get_class_hierarchy("ASampleActor")
    assert "ASampleActor" in result


# ── get_symbol_context ───────────────────────────────────────────────────

def test_get_symbol_context():
    result = server.get_symbol_context("DoSomething")
    assert "DoSomething" in result


# ── get_module_info ──────────────────────────────────────────────────────

def test_get_module_info():
    result = server.get_module_info("sample_ue_source")
    assert "sample_ue_source" in result


# ── find_references (no crash) ───────────────────────────────────────────

def test_find_references_no_crash():
    result = server.find_references("ASampleActor")
    assert isinstance(result, str)


# ── find_callers (no crash) ──────────────────────────────────────────────

def test_find_callers_no_crash():
    result = server.find_callers("DoSomething")
    assert isinstance(result, str)


def test_find_callers_smart_no_results_message():
    """find_callers should show a helpful message when no callers found."""
    result = server.find_callers("GetHealth")
    # GetHealth has no callers in our fixture
    assert "No direct" in result or "delegates" in result or "Blueprints" in result
    # Should NOT be just the old plain message
    assert result != "No callers found for 'GetHealth'."


# ── find_callees (no crash) ──────────────────────────────────────────────

def test_find_callees_no_crash():
    result = server.find_callees("DoSomething")
    assert isinstance(result, str)


# ── read_file ────────────────────────────────────────────────────────────

def test_read_file_by_full_path():
    """read_file should read lines from a file by its full path."""
    result = server.read_file(str(FIXTURES / "SampleActor.h"))
    assert "ASampleActor" in result
    assert "DoSomething" in result


def test_read_file_by_suffix():
    """read_file should resolve partial paths against the DB."""
    result = server.read_file("SampleActor.h")
    assert "ASampleActor" in result


def test_read_file_line_range():
    """read_file with start/end should return only those lines."""
    result = server.read_file(str(FIXTURES / "SampleActor.cpp"), start_line=11, end_line=19)
    assert "DoSomething" in result
    assert "InternalHelper" not in result


def test_read_file_not_found():
    """read_file should return a helpful message for unknown paths."""
    result = server.read_file("NonExistent.h")
    assert "not found" in result.lower() or "No file" in result


# ── search_source mode parameter ─────────────────────────────────────────

def test_search_source_substring_mode():
    """search_source with mode='substring' should find exact multi-token patterns."""
    result = server.search_source("void DoSomething(float DeltaTime)", mode="substring")
    assert "DoSomething" in result


def test_search_source_regex_mode():
    """search_source with mode='regex' should find regex patterns."""
    result = server.search_source(r"void\s+\w+\(float", mode="regex")
    assert "DoSomething" in result or "SampleActor" in result


def test_search_source_fts_mode_default():
    """search_source with default mode should work as before (FTS)."""
    result = server.search_source("DoSomething")
    assert "DoSomething" in result


# ── search_source module/path/kind filtering ──────────────────────────

def test_search_source_module_filter():
    """search_source with module filter should only return results from that module."""
    result = server.search_source("ASampleActor", module="sample_ue_source")
    assert "ASampleActor" in result


def test_search_source_module_filter_excludes():
    """search_source with wrong module should return no results."""
    result = server.search_source("ASampleActor", module="NonExistentModule")
    assert "No results" in result


def test_search_source_path_filter():
    """search_source with path_filter should scope results."""
    result = server.search_source("ASampleActor", path_filter="SampleActor")
    assert "ASampleActor" in result


def test_search_source_symbol_kind_filter():
    """search_source with symbol_kind should filter symbol results by kind."""
    result = server.search_source("ASampleActor", symbol_kind="class")
    assert "[class]" in result


def test_search_source_symbol_kind_function():
    """search_source with symbol_kind='function' filters to functions."""
    result = server.search_source("DoSomething", symbol_kind="function")
    assert "DoSomething" in result
