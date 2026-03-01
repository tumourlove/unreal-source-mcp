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


# ── find_callees (no crash) ──────────────────────────────────────────────

def test_find_callees_no_crash():
    result = server.find_callees("DoSomething")
    assert isinstance(result, str)
