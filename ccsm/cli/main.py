"""CLI entry point for ccsm.

Provides subcommands:
    ccsm tui      — Launch the TUI (default)
    ccsm list     — List sessions (CLI mode)
    ccsm resume   — Resume a session by ID
"""

from typing import Optional

import click

from ccsm import __version__


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="ccsm")
@click.option("--lang", type=click.Choice(["zh-CN", "en"]), default=None,
              help="UI and prompt language (default: zh-CN, or CCSM_LANG env)")
@click.pass_context
def cli(ctx: click.Context, lang: Optional[str]) -> None:
    """Claude Code Session Manager — manage your Claude sessions."""
    if lang:
        from ccsm.core.i18n import set_language
        set_language(lang)
    if ctx.invoked_subcommand is None:
        # Default: launch TUI
        ctx.invoke(tui)


@cli.command()
def tui() -> None:
    """Launch the TUI session manager."""
    from ccsm.tui.app import run

    run()


@cli.command(name="list")
@click.option("--worktree", "-w", default=None, help="Filter by worktree name")
@click.option("--status", "-s", default=None, help="Filter by status")
def list_sessions(worktree: Optional[str], status: Optional[str]) -> None:
    """List sessions in CLI mode."""
    click.echo("ccsm list — coming in Batch 3")


@cli.command()
@click.argument("session_id")
def resume(session_id: str) -> None:
    """Resume a Claude Code session by ID."""
    click.echo(f"ccsm resume {session_id} — coming in Batch 3")


if __name__ == "__main__":
    cli()
