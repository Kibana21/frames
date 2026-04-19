"""Smoke test: every registered NATIVE block renders without error and
produces the §6.9 generated-file contract shape.
"""

from __future__ import annotations

import pytest

from framecraft import BlockId, Provenance
from framecraft.registry import REGISTRY


def _minimal_props_for(bid: BlockId) -> dict:
    """Enough keys to satisfy each block's required_props."""
    match bid:
        case BlockId.TITLE_CARD:
            return {"headline": "Test headline"}
        case BlockId.END_CARD:
            return {"tagline": "Fin."}
        case BlockId.LOWER_THIRD:
            return {"name": "Alex Doe", "role": "CTO"}
        case BlockId.GRADIENT_BG:
            return {}
        case BlockId.GRAIN_OVERLAY:
            return {}
        case _:
            return {}


@pytest.mark.parametrize("bid", sorted(REGISTRY, key=lambda b: b.value))
def test_block_template_renders(bid: BlockId) -> None:
    spec = REGISTRY[bid]
    if spec.provenance is not Provenance.NATIVE:
        pytest.skip("CATALOG renderer arrives in M2")

    assert spec.template is not None
    html = spec.template(_minimal_props_for(bid), 0, 1920, 1080, 4.0)

    assert html.startswith("<template id=")
    assert f'data-composition-id="scene-00-{bid.value}"' in html
    assert 'data-width="1920"' in html
    assert 'data-height="1080"' in html
    assert 'window.__timelines[' in html
    assert html.rstrip().endswith("</template>")
    # No stray {{ or }} that would mean an f-string escaping bug leaked into output.
    assert "{{" not in html
    assert "}}" not in html


@pytest.mark.parametrize("bid", sorted(REGISTRY, key=lambda b: b.value))
def test_block_template_rejects_bad_props(bid: BlockId) -> None:
    spec = REGISTRY[bid]
    if spec.provenance is not Provenance.NATIVE:
        pytest.skip("CATALOG renderer arrives in M2")
    assert spec.required_props is not None

    # Feed a deliberately broken payload (unknown key is ignored by Pydantic
    # unless the model sets extra="forbid"; use an impossible value instead).
    match bid:
        case BlockId.TITLE_CARD:
            with pytest.raises(Exception):
                spec.template({"headline": ""}, 0, 1920, 1080, 3.0)
        case BlockId.END_CARD:
            with pytest.raises(Exception):
                spec.template({"tagline": ""}, 0, 1920, 1080, 3.0)
        case BlockId.LOWER_THIRD:
            with pytest.raises(Exception):
                spec.template({"name": ""}, 0, 1920, 1080, 3.0)
        case BlockId.GRADIENT_BG:
            with pytest.raises(Exception):
                spec.template({"angle_deg": 999}, 0, 1920, 1080, 3.0)
        case BlockId.GRAIN_OVERLAY:
            with pytest.raises(Exception):
                spec.template({"opacity": 5.0}, 0, 1920, 1080, 3.0)
        case _:
            pytest.skip(f"no failure case defined for {bid}")
