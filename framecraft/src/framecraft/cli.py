"""Typer root. See `.claude/plans/06a-cli-core.md`."""

from __future__ import annotations

import sys
from typing import NoReturn

import typer
from rich.console import Console

from framecraft.cli_catalog import catalog
from framecraft.cli_compose import compose
from framecraft.cli_doctor import doctor
from framecraft.cli_from_plan import from_plan
from framecraft.cli_preview import preview
from framecraft.cli_render import render
from framecraft.exit_codes import FrameCraftExit

app = typer.Typer(
    help="FrameCraft — turn situations into Hyperframes projects.",
    no_args_is_help=True,
    add_completion=False,
)

app.command("compose")(compose)
app.command("from-plan")(from_plan)
app.command("render")(render)
app.command("preview")(preview)
app.command("catalog")(catalog)
app.command("doctor")(doctor)


def main() -> NoReturn:
    try:
        app()
    except FrameCraftExit as e:
        Console(stderr=True).print(f"[red]{e.message}[/red]")
        sys.exit(e.code)
    except typer.Exit:
        raise
    except Exception as e:
        Console(stderr=True).print(f"[red]unexpected: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
