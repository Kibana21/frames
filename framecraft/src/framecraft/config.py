"""framecraft.yaml loader. See `.claude/plans/06a-cli-core.md` §16–18."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import platformdirs
import yaml
from pydantic import BaseModel, Field

from framecraft.schema import Aspect, Mood

_log = logging.getLogger("framecraft.config")

_USER_CONFIG_DIR = Path(platformdirs.user_config_dir("framecraft"))
_USER_CONFIG_PATH = _USER_CONFIG_DIR / "config.yaml"
_PROJECT_CONFIG_NAME = "framecraft.yaml"

_KNOWN_TOP_KEYS = frozenset({"defaults", "brand"})


class ConfigDefaults(BaseModel):
    aspect: Aspect | None = None
    duration: float | None = None
    fps: int | None = None
    mood: Mood | None = None
    provider: str | None = None


class ConfigBrand(BaseModel):
    logo: Path | None = None
    palette: str | None = None
    font: str | None = None


class FrameCraftConfig(BaseModel):
    defaults: ConfigDefaults = Field(default_factory=ConfigDefaults)
    brand: ConfigBrand = Field(default_factory=ConfigBrand)


def load_config(
    project_dir: Path | None = None,
    *,
    no_config: bool = False,
) -> FrameCraftConfig:
    """Load and merge framecraft.yaml config files.

    Merge order (lowest priority first): user-global → project-local.
    CLI args (handled by callers) always override.
    """
    if no_config:
        return FrameCraftConfig()

    merged: dict[str, Any] = {}

    if _USER_CONFIG_PATH.exists():
        _deep_merge(merged, _load_yaml(_USER_CONFIG_PATH))

    project_path = (project_dir or Path.cwd()) / _PROJECT_CONFIG_NAME
    if project_path.exists():
        _deep_merge(merged, _load_yaml(project_path))

    return FrameCraftConfig.model_validate(merged)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        _log.warning("Could not parse config %s: %s — skipped.", path, e)
        return {}

    if not isinstance(data, dict):
        _log.warning("Config %s is not a YAML mapping — skipped.", path)
        return {}

    for key in data:
        if key not in _KNOWN_TOP_KEYS:
            _log.warning("Unknown config key %r in %s — ignored.", key, path)

    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    for key, val in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
