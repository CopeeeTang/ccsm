"""CCSM Textual TUI Application.

Main entry point for the TUI. Creates the Textual app with
Claude Native theme and three-panel layout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from textual.app import App, ComposeResult

from ccsm.tui.screens.main import MainScreen


class CCSMApp(App):
    """Claude Code Session Manager — Textual TUI."""

    TITLE = "CCSM"
    SUB_TITLE = "Claude Code Session Manager"

    # Load the Claude Native theme CSS
    CSS_PATH = Path(__file__).parent / "styles" / "claude_native.tcss"

    SCREENS = {"main": MainScreen}

    BINDINGS = [
        ("t", "toggle_theme", "Theme"),
    ]

    def on_mount(self) -> None:
        """Push the main screen on startup."""
        self.push_screen("main")

    def action_toggle_theme(self) -> None:
        """Toggle light and dark mode."""
        if self.has_class("-light-theme"):
            self.remove_class("-light-theme")
        else:
            self.add_class("-light-theme")


def run() -> None:
    """Launch the CCSM TUI application.

    If the user selects 'Resume' (r), the app returns the session_id.
    We then launch `claude --resume {session_id}` AFTER Textual fully exits,
    ensuring the terminal is properly restored before spawning a new process.
    """
    app = CCSMApp()
    result = app.run()

    # If result is a session_id string, launch claude --resume
    if result and isinstance(result, str):
        subprocess.run(["claude", "--resume", result])


if __name__ == "__main__":
    run()
