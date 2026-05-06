"""Configuration loading and validation for Gatto Farioli."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.yaml"
DEFAULT_ENV_PATH = PROJECT_DIR / ".env"


class ConfigError(ValueError):
    """Raised when config.yaml is missing a required section or value."""


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load .env and config.yaml, then validate the Session 1 requirements."""
    load_dotenv(DEFAULT_ENV_PATH)
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    """Validate the minimal keys needed for a safe Session 1 news run."""
    required_sections = ["portfolio", "theses", "watchlist", "news_sources", "alerts", "llm", "schedule"]
    missing = [section for section in required_sections if section not in config]
    if missing:
        raise ConfigError(f"Missing required config sections: {', '.join(missing)}")

    tier_1 = config.get("news_sources", {}).get("tier_1", [])
    if not isinstance(tier_1, list) or not tier_1:
        raise ConfigError("news_sources.tier_1 must be a non-empty list of RSS URLs")

    positions = config.get("portfolio", {}).get("positions", [])
    if not isinstance(positions, list):
        raise ConfigError("portfolio.positions must be a list")

    for pos in positions:
        for field in ("ticker", "shares", "avg_cost", "thesis", "conviction"):
            if field not in pos:
                raise ConfigError(f"Portfolio position missing required field: {field}")
