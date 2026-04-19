"""`framecraft catalog` — print the block registry."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from framecraft.blocks._spec import BlockSpec
from framecraft.registry import ARCHETYPE_BLOCK_POLICY, REGISTRY

_console = Console()


def catalog(
    as_json: bool = typer.Option(False, "--json", help="Emit the registry as JSON."),
) -> None:
    if as_json:
        payload = {
            bid.value: {
                "id": spec.id.value,
                "category": spec.category.value,
                "provenance": spec.provenance.value,
                "synopsis": spec.synopsis,
                "suggested_duration": list(spec.suggested_duration),
                "aspect_preferred": [a.value for a in spec.aspect_preferred],
                "fallback_block_id": (
                    spec.fallback_block_id.value if spec.fallback_block_id else None
                ),
            }
            for bid, spec in REGISTRY.items()
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    table = Table(title="framecraft catalog", show_header=True, header_style="bold")
    table.add_column("id")
    table.add_column("category")
    table.add_column("provenance")
    table.add_column("archetypes")
    table.add_column("aspects")
    table.add_column("duration (s)")
    table.add_column("synopsis")
    for bid, spec in sorted(REGISTRY.items(), key=lambda kv: kv[0].value):
        lo, hi = spec.suggested_duration
        table.add_row(
            bid.value,
            spec.category.value,
            spec.provenance.value,
            _archetypes_for(spec),
            ", ".join(a.value for a in spec.aspect_preferred),
            f"{lo:g}–{hi:g}",
            spec.synopsis,
        )
    _console.print(table)


def _archetypes_for(spec: BlockSpec) -> str:
    arches = [
        a.value for a, cats in ARCHETYPE_BLOCK_POLICY.items()
        if spec.category in cats
    ]
    return ", ".join(sorted(arches)) or "—"
