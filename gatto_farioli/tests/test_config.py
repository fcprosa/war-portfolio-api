"""Config loading and validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config import ConfigError, load_config, validate_config


def test_validate_config_accepts_minimal(minimal_config: dict) -> None:
    validate_config(minimal_config)


def test_validate_config_rejects_missing_section(minimal_config: dict) -> None:
    cfg = dict(minimal_config)
    del cfg["news_sources"]
    with pytest.raises(ConfigError, match="Missing required config sections"):
        validate_config(cfg)


def test_validate_config_rejects_empty_tier_1(minimal_config: dict) -> None:
    cfg = dict(minimal_config)
    cfg["news_sources"] = {"tier_1": []}
    with pytest.raises(ConfigError, match="tier_1"):
        validate_config(cfg)


def test_validate_config_rejects_bad_position_shape(minimal_config: dict) -> None:
    cfg = dict(minimal_config)
    cfg["portfolio"] = {"positions": [{"ticker": "XOM"}]}
    with pytest.raises(ConfigError, match="missing required field"):
        validate_config(cfg)


def test_load_config_from_file(tmp_path: Path, minimal_config: dict) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(minimal_config), encoding="utf-8")
    loaded = load_config(path)
    assert loaded["news_sources"]["tier_1"] == minimal_config["news_sources"]["tier_1"]


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "missing.yaml")
