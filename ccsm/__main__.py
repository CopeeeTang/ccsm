"""ccsm — Claude Code Session Manager

Usage:
    python -m ccsm          # Launch TUI
    python -m ccsm --help   # Show CLI help
"""

from ccsm.cli.main import cli

if __name__ == "__main__":
    cli()
