"""Unit tests for framecraft.yaml config loader. See `.claude/plans/06a-cli-core.md` §16–18."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from framecraft.config import FrameCraftConfig, load_config


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Basic loading
# ---------------------------------------------------------------------------


def test_no_config_returns_defaults(tmp_path):
    with patch("framecraft.config._USER_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
        cfg = load_config(project_dir=tmp_path)
    assert cfg.defaults.aspect is None
    assert cfg.brand.font is None


def test_no_config_flag_skips_both_files(tmp_path):
    proj_yaml = tmp_path / "framecraft.yaml"
    _write_yaml(proj_yaml, "defaults:\n  provider: gemini\n")
    with patch("framecraft.config._USER_CONFIG_PATH", tmp_path / "user.yaml"):
        _write_yaml(tmp_path / "user.yaml", "defaults:\n  provider: anthropic\n")
        cfg = load_config(project_dir=tmp_path, no_config=True)
    assert cfg.defaults.provider is None


def test_project_local_loaded(tmp_path):
    _write_yaml(tmp_path / "framecraft.yaml", "defaults:\n  provider: gemini\n  duration: 15\n")
    with patch("framecraft.config._USER_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
        cfg = load_config(project_dir=tmp_path)
    assert cfg.defaults.provider == "gemini"
    assert cfg.defaults.duration == 15.0


def test_user_global_loaded(tmp_path):
    user_cfg = tmp_path / "user" / "config.yaml"
    _write_yaml(user_cfg, "defaults:\n  provider: anthropic\n")
    with patch("framecraft.config._USER_CONFIG_PATH", user_cfg):
        cfg = load_config(project_dir=tmp_path)
    assert cfg.defaults.provider == "anthropic"


# ---------------------------------------------------------------------------
# Merge order: project-local overrides user-global
# ---------------------------------------------------------------------------


def test_project_local_overrides_user_global(tmp_path):
    user_cfg = tmp_path / "user.yaml"
    _write_yaml(user_cfg, "defaults:\n  provider: anthropic\n  duration: 30\n")
    _write_yaml(tmp_path / "framecraft.yaml", "defaults:\n  provider: gemini\n")

    with patch("framecraft.config._USER_CONFIG_PATH", user_cfg):
        cfg = load_config(project_dir=tmp_path)

    assert cfg.defaults.provider == "gemini"  # project-local wins
    assert cfg.defaults.duration == 30.0       # user-global preserved


def test_brand_merged_correctly(tmp_path):
    user_cfg = tmp_path / "user.yaml"
    _write_yaml(user_cfg, "brand:\n  font: Inter\n  palette: \"#000000,#FFFFFF,#FF0000\"\n")
    _write_yaml(tmp_path / "framecraft.yaml", "brand:\n  font: Roboto\n")

    with patch("framecraft.config._USER_CONFIG_PATH", user_cfg):
        cfg = load_config(project_dir=tmp_path)

    assert cfg.brand.font == "Roboto"          # project-local wins
    assert cfg.brand.palette is not None        # user-global preserved


# ---------------------------------------------------------------------------
# Unknown key warns
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_warns(tmp_path, caplog):
    _write_yaml(tmp_path / "framecraft.yaml", "unknown_key: value\ndefaults:\n  provider: gemini\n")
    with patch("framecraft.config._USER_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
        with caplog.at_level(logging.WARNING, logger="framecraft.config"):
            cfg = load_config(project_dir=tmp_path)
    assert "unknown_key" in caplog.text
    assert cfg.defaults.provider == "gemini"


# ---------------------------------------------------------------------------
# Malformed YAML — graceful degradation
# ---------------------------------------------------------------------------


def test_malformed_yaml_skipped_gracefully(tmp_path, caplog):
    (tmp_path / "framecraft.yaml").write_text("{ invalid: yaml: content :", encoding="utf-8")
    with patch("framecraft.config._USER_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
        with caplog.at_level(logging.WARNING, logger="framecraft.config"):
            cfg = load_config(project_dir=tmp_path)
    assert isinstance(cfg, FrameCraftConfig)


def test_non_mapping_yaml_skipped_gracefully(tmp_path, caplog):
    (tmp_path / "framecraft.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
    with patch("framecraft.config._USER_CONFIG_PATH", tmp_path / "nonexistent.yaml"):
        with caplog.at_level(logging.WARNING, logger="framecraft.config"):
            cfg = load_config(project_dir=tmp_path)
    assert isinstance(cfg, FrameCraftConfig)
