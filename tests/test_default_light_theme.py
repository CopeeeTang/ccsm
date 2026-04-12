"""CCSM should start in light theme by default, and persist preference."""
from __future__ import annotations

import pytest

from ccsm.tui.app import CCSMApp


@pytest.mark.asyncio
async def test_app_starts_with_light_theme_class(tmp_path, monkeypatch):
    """On first run (no config), the app should default to light theme."""
    # Isolate config to tmp_path so we don't mutate ~/.ccsm
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    import ccsm.core.config as config_mod
    importlib.reload(config_mod)

    async with CCSMApp().run_test() as pilot:
        assert pilot.app.has_class("-light-theme"), (
            "App should start with -light-theme class by default"
        )


@pytest.mark.asyncio
async def test_theme_preference_persists_across_runs(tmp_path, monkeypatch):
    """After toggling to dark, next startup should remember dark."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    import ccsm.core.config as config_mod
    importlib.reload(config_mod)

    # First run: default light, then toggle to dark
    async with CCSMApp().run_test() as pilot:
        assert pilot.app.has_class("-light-theme")
        pilot.app.action_toggle_theme()
        await pilot.pause()
        assert not pilot.app.has_class("-light-theme")

    # Reload config module so CONFIG_PATH re-reads HOME
    importlib.reload(config_mod)

    # Second run: should remember dark
    async with CCSMApp().run_test() as pilot:
        assert not pilot.app.has_class("-light-theme"), (
            "Theme preference should persist across runs"
        )
