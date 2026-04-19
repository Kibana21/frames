"""CATALOG block install + slot injection. See `.claude/plans/04-assembler.md` §8–9."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from framecraft.blocks._spec import SlotSpec
from framecraft.providers.base import LLMProvider
from framecraft.rendering.html_walker import merge_style, parse, select_one, serialize
from framecraft.subprocess_helpers import run_npx


class CatalogSlotError(Exception):
    """Raised when a slot selector matches no element in the installed HTML."""


class CatalogHashError(Exception):
    """Raised when a catalog block's SHA-256 doesn't match the pinned hash."""


def install_catalog_block(
    catalog_id: str,
    catalog_version: str,
    catalog_hash: str,
    out_dir: Path,
) -> Path:
    """Install a catalog block via `npx hyperframes add`. Return the primary HTML path.

    Idempotent: skips install if `.framecraft/installed/<catalog_id>.json` exists
    with a matching hash.
    """
    installed_dir = out_dir / ".framecraft" / "installed"
    installed_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = installed_dir / f"{catalog_id}.json"

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("hash") == catalog_hash:
            return out_dir / manifest["primary_file"]

    before = _snapshot_tree(out_dir)

    run_npx(
        ["hyperframes", "add", catalog_id, "--version", catalog_version, "--non-interactive"],
        cwd=out_dir,
    )

    after = _snapshot_tree(out_dir)
    new_files = sorted(set(after) - set(before))

    actual_hash = _hash_files([(p, (out_dir / p).read_bytes()) for p in new_files])
    if actual_hash != catalog_hash:
        raise CatalogHashError(
            f"Catalog block `{catalog_id}` hash mismatch: "
            f"expected `{catalog_hash}`, got `{actual_hash}`. "
            f"Re-pin with `framecraft doctor --snapshot {catalog_id}`."
        )

    primary = next(
        (p for p in new_files if p.startswith("compositions/") and p.endswith(".html")),
        new_files[0] if new_files else "",
    )

    manifest_data = {
        "catalog_id": catalog_id,
        "version": catalog_version,
        "hash": actual_hash,
        "files": new_files,
        "primary_file": primary,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")
    return out_dir / primary


def inject_slots(
    installed_html: str,
    slots: dict[str, SlotSpec],
    props: dict[str, Any],
    provider: LLMProvider,
    polish_cache: dict[str, str],
) -> tuple[str, int, int]:
    """Inject typed slot values into an installed catalog HTML template.

    Operates on a parsed DOM (no regex). Returns (modified_html, cache_hits, cache_misses).
    """
    soup = parse(installed_html)
    cache_hits = 0
    cache_misses = 0

    for slot_name, spec in slots.items():
        node = select_one(soup, spec.selector)
        if node is None:
            raise CatalogSlotError(
                f"slot `{slot_name}` selector `{spec.selector}` matched no element"
            )

        raw = str(props.get(slot_name, ""))
        value, hits, misses = _polish_if_needed(raw, spec, provider, polish_cache, slot_name)
        cache_hits += hits
        cache_misses += misses

        match spec.kind:
            case "text":
                node.string = value
            case "css_var":
                node["style"] = merge_style(str(node.get("style", "")), {spec.target: value})
            case "attr":
                node[spec.target] = value
            case "asset_path":
                node[spec.target] = value

    return serialize(soup), cache_hits, cache_misses


def _polish_if_needed(
    raw: str,
    spec: SlotSpec,
    provider: LLMProvider,
    cache: dict[str, str],
    field_name: str,
) -> tuple[str, int, int]:
    if not spec.llm_polish:
        return raw, 0, 0
    cache_key = f"{field_name}::{hashlib.sha256(raw.encode()).hexdigest()[:12]}"
    if cache_key in cache:
        return cache[cache_key], 1, 0
    polished = _call_polish(raw, field_name, spec, provider)
    cache[cache_key] = polished
    return polished, 0, 1


def _call_polish(raw: str, field_name: str, spec: SlotSpec, provider: LLMProvider) -> str:
    from framecraft.prompts import load_common, load_primer

    system = load_common("assembler")
    msg = (
        f"Polish the following {field_name!r} value for use in a video composition.\n"
        f"Max {spec.max_length or 200} characters. Return only the polished text, no quotes.\n\n"
        f"Original: {raw}"
    )
    resp = provider.complete(
        [{"role": "user", "content": msg}],
        system=system,
        cache_segments=[load_primer()],
        model=None,
    )
    return resp.text.strip()


def _snapshot_tree(out_dir: Path) -> set[str]:
    return {
        str(p.relative_to(out_dir))
        for p in out_dir.rglob("*")
        if p.is_file() and ".framecraft" not in p.parts
    }


def _hash_files(files: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for path, content in sorted(files):
        h.update(path.encode("utf-8"))
        h.update(content)
    return h.hexdigest()
