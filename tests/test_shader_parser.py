"""Tests for the HLSL shader parser."""

from pathlib import Path

import pytest

from unreal_source_mcp.indexer.shader_parser import ShaderParser

FIXTURES = Path(__file__).parent / "fixtures" / "sample_ue_source"
SAMPLE_SHADER = FIXTURES / "SampleShader.usf"


@pytest.fixture
def parsed():
    parser = ShaderParser()
    return parser.parse_file(SAMPLE_SHADER)


class TestShaderParserFunctions:
    def test_encode_normal_found(self, parsed):
        names = [s.name for s in parsed.symbols if s.kind == "function"]
        assert "EncodeNormal" in names

    def test_decode_gbuffer_normal_found(self, parsed):
        names = [s.name for s in parsed.symbols if s.kind == "function"]
        assert "DecodeGBufferNormal" in names

    def test_main_ps_found(self, parsed):
        names = [s.name for s in parsed.symbols if s.kind == "function"]
        assert "MainPS" in names

    def test_function_has_signature(self, parsed):
        fn = next(s for s in parsed.symbols if s.name == "EncodeNormal")
        assert "float3" in fn.signature
        assert "Normal" in fn.signature

    def test_function_line_range(self, parsed):
        fn = next(s for s in parsed.symbols if s.name == "EncodeNormal")
        assert fn.line is not None
        assert fn.end_line is not None
        assert fn.end_line > fn.line


class TestShaderParserStructs:
    def test_gbuffer_data_found(self, parsed):
        names = [s.name for s in parsed.symbols if s.kind == "struct"]
        assert "FGBufferData" in names

    def test_struct_line_range(self, parsed):
        st = next(s for s in parsed.symbols if s.name == "FGBufferData")
        assert st.end_line > st.line


class TestShaderParserDefines:
    def test_gbuffer_has_tangent_found(self, parsed):
        names = [s.name for s in parsed.symbols if s.kind == "define"]
        assert "GBUFFER_HAS_TANGENT" in names

    def test_define_value_in_signature(self, parsed):
        d = next(s for s in parsed.symbols if s.name == "GBUFFER_HAS_TANGENT")
        assert "1" in d.signature


class TestShaderParserIncludes:
    def test_common_ush_included(self, parsed):
        assert any("Common.ush" in inc for inc in parsed.includes)

    def test_deferred_shading_included(self, parsed):
        assert any("DeferredShadingCommon.ush" in inc for inc in parsed.includes)

    def test_include_count(self, parsed):
        assert len(parsed.includes) == 2


class TestShaderParserDocstrings:
    def test_decode_gbuffer_normal_docstring(self, parsed):
        fn = next(s for s in parsed.symbols if s.name == "DecodeGBufferNormal")
        assert fn.docstring is not None
        assert "octahedron" in fn.docstring.lower()

    def test_encode_normal_line_comment(self, parsed):
        fn = next(s for s in parsed.symbols if s.name == "EncodeNormal")
        assert fn.docstring is not None
        assert "GBuffer" in fn.docstring
