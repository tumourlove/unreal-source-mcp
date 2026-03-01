"""Tests for config module."""

import os
from unittest.mock import patch


def test_explicit_version_env_var():
    """UE_VERSION env var should take priority."""
    with patch.dict(os.environ, {
        "UE_VERSION": "5.4",
        "UE_SOURCE_PATH": "/some/custom/path",
    }):
        import importlib
        import unreal_source_mcp.config as cfg
        importlib.reload(cfg)
        db_path = cfg.get_db_path()
        assert "ue_5.4" in db_path.name


def test_version_detected_from_path():
    """Version should be extracted from UE_SOURCE_PATH."""
    with patch.dict(os.environ, {
        "UE_SOURCE_PATH": "C:/Program Files/UE_5.7/Engine/Source",
    }, clear=False):
        import importlib
        import unreal_source_mcp.config as cfg
        os.environ.pop("UE_VERSION", None)
        importlib.reload(cfg)
        db_path = cfg.get_db_path()
        assert "ue_5.7" in db_path.name


def test_unknown_version_fallback():
    """Custom paths without version info should get 'unknown'."""
    with patch.dict(os.environ, {
        "UE_SOURCE_PATH": "/custom/engine/Source",
    }, clear=False):
        import importlib
        import unreal_source_mcp.config as cfg
        os.environ.pop("UE_VERSION", None)
        importlib.reload(cfg)
        db_path = cfg.get_db_path()
        assert "ue_unknown" in db_path.name
