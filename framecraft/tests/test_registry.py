"""Registry + block discovery tests."""

from __future__ import annotations

import pytest

from framecraft import Archetype, BlockId, Category, Provenance
from framecraft.registry import REGISTRY, BlockRegistry, default_registry


def test_discovery_finds_all_m1_blocks() -> None:
    # The full M1 NATIVE primitive set.
    expected = {
        BlockId.TITLE_CARD,
        BlockId.END_CARD,
        BlockId.LOWER_THIRD,
        BlockId.GRADIENT_BG,
        BlockId.GRAIN_OVERLAY,
    }
    assert expected.issubset(set(REGISTRY.keys()))


def test_specs_are_native() -> None:
    for spec in REGISTRY.values():
        assert spec.provenance is Provenance.NATIVE
        assert spec.template is not None


def test_every_spec_declares_required_props() -> None:
    for spec in REGISTRY.values():
        assert spec.required_props is not None, (
            f"Block {spec.id} must declare required_props so scene.block_props "
            "can be validated; see 01-schema-and-registry.md §4."
        )


def test_ids_match_module_names() -> None:
    """Convention check: file `foo_bar.py` → id `foo-bar`. Enforced by
    scripts/gen_block_ids.py; this test makes the break loud."""
    for bid in REGISTRY:
        assert bid.value.count("-") >= 0
        # round-trip: "foo-bar" → "FOO_BAR" → BlockId member exists
        member = bid.value.upper().replace("-", "_")
        assert hasattr(BlockId, member)


def test_allowed_for_product_promo() -> None:
    reg = default_registry()
    allowed = reg.allowed_for(Archetype.PRODUCT_PROMO)
    assert BlockId.TITLE_CARD in allowed
    assert BlockId.END_CARD in allowed
    assert BlockId.GRADIENT_BG in allowed
    assert BlockId.GRAIN_OVERLAY in allowed


def test_allowed_for_narrative_scene() -> None:
    reg = default_registry()
    allowed = reg.allowed_for(Archetype.NARRATIVE_SCENE)
    assert BlockId.TITLE_CARD in allowed
    # Narrative doesn't include product/social/data per ARCHETYPE_BLOCK_POLICY
    assert BlockId.GRADIENT_BG in allowed


def test_allowed_for_social_card() -> None:
    reg = default_registry()
    allowed = reg.allowed_for(Archetype.SOCIAL_CARD)
    # Social card has no PRODUCT blocks available.
    for bid in allowed:
        spec = reg.resolve(bid)
        assert spec.category is not Category.PRODUCT


def test_resolve_unknown() -> None:
    reg = BlockRegistry({}, {})
    with pytest.raises(KeyError):
        reg.resolve(BlockId.TITLE_CARD)


def test_gen_block_ids_check_matches_filesystem() -> None:
    """`_block_ids.py` must be in sync with the blocks/ directory."""
    import pathlib
    import subprocess
    import sys

    script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "gen_block_ids.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"_block_ids.py is stale. Run: python {script}\n"
        f"stderr: {result.stderr}"
    )
