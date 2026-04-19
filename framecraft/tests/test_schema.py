"""01 schema tests — every validator the plan asks for."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from framecraft import Archetype, Aspect, BlockId, Brief, Palette, Scene, SceneGraph, Typography
from framecraft.registry import default_registry
from framecraft.schema import TransitionCue, TransitionId


def _base_brief() -> Brief:
    return Brief(situation="a test situation that is long enough", aspect=Aspect.AR_16_9, duration=10)


def _base_scenes() -> list[Scene]:
    return [
        Scene(
            index=0, block_id=BlockId.TITLE_CARD, start=0.0, duration=6.0,
            block_props={"headline": "Hello"},
        ),
        Scene(
            index=1, block_id=BlockId.END_CARD, start=6.0, duration=4.0,
            block_props={"tagline": "Fin."},
        ),
    ]


def _happy_graph() -> SceneGraph:
    return SceneGraph(
        brief=_base_brief(),
        archetype=Archetype.NARRATIVE_SCENE,
        aspect=Aspect.AR_16_9,
        canvas=(1920, 1080),
        duration=10.0,
        scenes=_base_scenes(),
    )


def test_happy_path() -> None:
    g = _happy_graph()
    assert g.canvas == g.aspect.dimensions
    assert sum(s.duration for s in g.scenes) == g.duration


def test_duration_mismatch() -> None:
    with pytest.raises(ValidationError, match="differs from graph duration"):
        SceneGraph(
            brief=_base_brief(),
            archetype=Archetype.NARRATIVE_SCENE,
            aspect=Aspect.AR_16_9,
            canvas=(1920, 1080),
            duration=15.0,
            scenes=_base_scenes(),
        )


def test_canvas_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match aspect"):
        SceneGraph(
            brief=_base_brief(),
            archetype=Archetype.NARRATIVE_SCENE,
            aspect=Aspect.AR_16_9,
            canvas=(1080, 1920),
            duration=10.0,
            scenes=_base_scenes(),
        )


def test_scene_indices_non_contiguous() -> None:
    scenes = _base_scenes()
    scenes[1] = scenes[1].model_copy(update={"index": 5})
    with pytest.raises(ValidationError, match="contiguous from 0"):
        SceneGraph(
            brief=_base_brief(),
            archetype=Archetype.NARRATIVE_SCENE,
            aspect=Aspect.AR_16_9,
            canvas=(1920, 1080),
            duration=10.0,
            scenes=scenes,
        )


def test_scene_starts_decreasing() -> None:
    scenes = [
        Scene(index=0, block_id=BlockId.TITLE_CARD, start=6.0, duration=4.0, block_props={"headline": "A"}),
        Scene(index=1, block_id=BlockId.END_CARD, start=0.0, duration=6.0, block_props={"tagline": "B"}),
    ]
    with pytest.raises(ValidationError, match="non-decreasing"):
        SceneGraph(
            brief=_base_brief(),
            archetype=Archetype.NARRATIVE_SCENE,
            aspect=Aspect.AR_16_9,
            canvas=(1920, 1080),
            duration=10.0,
            scenes=scenes,
        )


def test_transition_non_adjacent() -> None:
    with pytest.raises(ValidationError, match="adjacent"):
        TransitionCue(
            from_scene=0, to_scene=5, block_id=TransitionId.PLACEHOLDER, overlap=0.5
        )


def test_palette_bad_hex() -> None:
    with pytest.raises(ValidationError, match="#RRGGBB"):
        Palette(primary="not-a-hex", bg="#FFFFFF", accent="#000000")


def test_aspect_dimensions() -> None:
    assert Aspect.AR_16_9.dimensions == (1920, 1080)
    assert Aspect.AR_9_16.dimensions == (1080, 1920)
    assert Aspect.AR_1_1.dimensions == (1080, 1080)


def test_schema_hash_is_stable() -> None:
    from framecraft.schema import SCHEMA_HASH, compute_schema_hash

    assert SCHEMA_HASH == compute_schema_hash()


# --- block_props validation (new for 01) ---


def test_block_props_validation_happy() -> None:
    g = _happy_graph().validate_block_props_against(default_registry())
    # Canonicalized: defaults are filled in
    assert g.scenes[0].block_props["bg"] == "#09090C"
    assert g.scenes[0].block_props["fg"] == "#FFFFFF"


def test_block_props_validation_missing_required() -> None:
    scenes = [
        Scene(index=0, block_id=BlockId.TITLE_CARD, start=0, duration=6.0, block_props={}),
        Scene(index=1, block_id=BlockId.END_CARD, start=6.0, duration=4.0, block_props={"tagline": "X"}),
    ]
    g = SceneGraph(
        brief=_base_brief(),
        archetype=Archetype.NARRATIVE_SCENE,
        aspect=Aspect.AR_16_9,
        canvas=(1920, 1080),
        duration=10.0,
        scenes=scenes,
    )
    with pytest.raises(ValidationError, match="headline"):
        g.validate_block_props_against(default_registry())


def test_block_props_validation_too_long() -> None:
    scenes = [
        Scene(
            index=0, block_id=BlockId.TITLE_CARD, start=0, duration=6.0,
            block_props={"headline": "x" * 200},  # max_length=120
        ),
        Scene(
            index=1, block_id=BlockId.END_CARD, start=6.0, duration=4.0,
            block_props={"tagline": "X"},
        ),
    ]
    g = SceneGraph(
        brief=_base_brief(),
        archetype=Archetype.NARRATIVE_SCENE,
        aspect=Aspect.AR_16_9,
        canvas=(1920, 1080),
        duration=10.0,
        scenes=scenes,
    )
    with pytest.raises(ValidationError, match="120"):
        g.validate_block_props_against(default_registry())


# --- Typography Google Fonts soft-warn ---


def test_typography_unknown_font_warns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="framecraft.schema")
    Typography(headline="Totally Made Up Font Family XYZ")
    assert any("not in the built-in Google Fonts list" in r.message for r in caplog.records)


def test_typography_known_font_no_warn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING", logger="framecraft.schema")
    Typography(headline="Inter")
    assert not any("not in the built-in Google Fonts list" in r.message for r in caplog.records)
