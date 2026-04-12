"""UI preferences persistence at ~/.ccsm/config.json.

Stores lightweight user-facing UI preferences such as theme and language.
Writes are atomic (write-to-tmp then rename) so concurrent runs cannot
observe a half-written file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _config_path() -> Path:
    """Return the current config path, respecting HOME at call time.

    Resolving HOME lazily (instead of caching at import) means tests that
    monkeypatch HOME get a fresh path without needing to reload the module.
    """
    return Path.home() / ".ccsm" / "config.json"


# Kept for backwards compatibility — some callers may do
# `from ccsm.core.config import CONFIG_PATH`.
CONFIG_PATH = _config_path()

DEFAULTS: dict[str, Any] = {
    "theme": "light",       # "light" | "dark"
    "language": "zh-CN",    # "zh-CN" | "en"
}


def load_config() -> dict[str, Any]:
    """Load config from disk, falling back to defaults.

    Corrupt JSON or I/O errors are swallowed — we never want a broken
    config file to crash the TUI.
    """
    path = _config_path()
    if not path.exists():
        return dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        if isinstance(data, dict):
            merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config to disk atomically."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def get_pref(key: str, default: Any = None) -> Any:
    """Return a single preference value (or default)."""
    cfg = load_config()
    return cfg.get(key, default)


def set_pref(key: str, value: Any) -> None:
    """Update a single preference value, persisting the full config."""
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
