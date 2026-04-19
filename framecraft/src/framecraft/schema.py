"""Pydantic v2 type system — the single source of truth for plan.json.

See `.claude/plans/01-schema-and-registry.md`. M0 ships the minimal subset
needed for `compose --dry-run`; the full validator suite lands in M1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from framecraft._block_ids import BlockId, TransitionId

if TYPE_CHECKING:
    from framecraft.registry import BlockRegistry

_log = logging.getLogger("framecraft.schema")


# --- Enums -----------------------------------------------------------------


class Aspect(StrEnum):
    AR_16_9 = "16:9"
    AR_9_16 = "9:16"
    AR_1_1 = "1:1"

    @property
    def dimensions(self) -> tuple[int, int]:
        match self:
            case Aspect.AR_16_9:
                return (1920, 1080)
            case Aspect.AR_9_16:
                return (1080, 1920)
            case Aspect.AR_1_1:
                return (1080, 1080)


class Mood(StrEnum):
    CINEMATIC = "cinematic"
    PLAYFUL = "playful"
    SERIOUS = "serious"
    TECHNICAL = "technical"
    WARM = "warm"


class Archetype(StrEnum):
    NARRATIVE_SCENE = "narrative_scene"
    PRODUCT_PROMO = "product_promo"
    DATA_EXPLAINER = "data_explainer"
    UI_WALKTHROUGH = "ui_walkthrough"
    SOCIAL_CARD = "social_card"


class Provenance(StrEnum):
    NATIVE = "native"
    CATALOG = "catalog"


class Category(StrEnum):
    TITLE = "title"
    BACKGROUND = "background"
    BRANDING = "branding"
    PRODUCT = "product"
    DATA = "data"
    SOCIAL = "social"
    NOTIFICATION = "notification"
    TRANSITION = "transition"


# BlockId / TransitionId are re-exported from the generated `_block_ids.py`
# so type checkers see the canonical members. Regenerate after adding a new
# block file via `python scripts/gen_block_ids.py`.
__all_ids__ = ["BlockId", "TransitionId"]


# --- Brand / Typography ----------------------------------------------------


_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class Palette(BaseModel):
    primary: str
    bg: str
    accent: str

    @field_validator("primary", "bg", "accent")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX_RE.match(v):
            raise ValueError(f"expected #RRGGBB, got {v!r}")
        return v.upper()


# Non-exhaustive list of Google Fonts families we know ship the weight range
# we request. Unknown fonts still work (they fall back in the browser) but we
# log a one-time warning so users catch typos early.
_KNOWN_GOOGLE_FONTS: frozenset[str] = frozenset({
    "Inter", "IBM Plex Sans", "IBM Plex Mono", "IBM Plex Serif",
    "Roboto", "Roboto Mono", "Roboto Slab",
    "Montserrat", "Poppins", "Lato", "Open Sans", "Nunito",
    "Playfair Display", "Libre Baskerville", "Merriweather",
    "JetBrains Mono", "Fira Code", "Fira Sans",
    "Space Grotesk", "Space Mono", "DM Sans", "DM Mono", "DM Serif Display",
    "Work Sans", "Manrope", "Geist", "Geist Mono",
    "Crimson Pro", "EB Garamond", "Source Serif 4", "Source Sans 3",
})


class Typography(BaseModel):
    headline: str = "Inter"
    body: str = "Inter"
    weight_range: tuple[int, int] = (300, 900)

    @field_validator("headline", "body")
    @classmethod
    def _known_font(cls, v: str) -> str:
        if v not in _KNOWN_GOOGLE_FONTS:
            _log.warning(
                "typography.%s=%r is not in the built-in Google Fonts list. "
                "The browser will fall back if this name is wrong.",
                "font", v,
            )
        return v


class BrandKit(BaseModel):
    logo_path: Path | None = None
    palette: Palette | None = None
    typography: Typography | None = None


# --- Core graph types ------------------------------------------------------


class Scene(BaseModel):
    index: int = Field(ge=0)
    block_id: BlockId
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    track_index: int = Field(ge=1, default=1)
    block_props: dict[str, Any] = Field(default_factory=dict)
    # Polish cache (§6.4). Empty in M0 because no fields are llm_polish=True yet.
    polished: dict[str, str] = Field(default_factory=dict)


class TransitionCue(BaseModel):
    from_scene: int = Field(ge=0)
    to_scene: int = Field(ge=1)
    block_id: TransitionId
    overlap: float = Field(ge=0.3, le=1.5)

    @model_validator(mode="after")
    def _adjacent(self) -> TransitionCue:
        if self.to_scene != self.from_scene + 1:
            raise ValueError("transitions connect adjacent scenes only")
        return self


class Caption(BaseModel):
    start: float
    duration: float
    text: str


class Brief(BaseModel):
    situation: str = Field(min_length=3, max_length=2000)
    aspect: Aspect = Aspect.AR_16_9
    duration: float = Field(ge=3, le=300, default=20)
    fps: int = Field(default=30, ge=24, le=60)
    mood: Mood | None = None
    archetype: Archetype | None = None
    brand_kit: BrandKit | None = None
    music_path: Path | None = None
    music_volume: float = Field(default=0.4, ge=0, le=1)
    # Freeform creative directive passed to Director + Author prompts. Used by
    # --n-variants to produce distinct renditions from the same situation.
    # Backward-compatible default (None) leaves every prompt unchanged.
    style_seed: str | None = Field(default=None, max_length=400)


class SceneGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    brief: Brief
    archetype: Archetype
    aspect: Aspect
    canvas: tuple[int, int]
    duration: float = Field(gt=0)
    scenes: list[Scene] = Field(min_length=1, max_length=12)
    transitions: list[TransitionCue] = Field(default_factory=list)
    brand_kit: BrandKit | None = None

    @model_validator(mode="after")
    def _validate(self) -> SceneGraph:
        want_w, want_h = self.aspect.dimensions
        if self.canvas != (want_w, want_h):
            raise ValueError(
                f"canvas {self.canvas} does not match aspect {self.aspect} "
                f"({want_w}x{want_h})"
            )

        # scene.index is a 0-based contiguous sequence
        expected = list(range(len(self.scenes)))
        got = [s.index for s in self.scenes]
        if got != expected:
            raise ValueError(f"scene.index must be contiguous from 0; got {got}")

        # scene.start non-decreasing
        starts = [s.start for s in self.scenes]
        if starts != sorted(starts):
            raise ValueError(f"scene.start values must be non-decreasing; got {starts}")

        # duration budget within ±0.1s
        total_scene = sum(s.duration for s in self.scenes)
        total_overlap = sum(t.overlap for t in self.transitions)
        budget = total_scene - total_overlap
        if abs(budget - self.duration) > 0.1:
            raise ValueError(
                f"sum(scene.duration) - sum(transition.overlap) = {budget:.3f} "
                f"differs from graph duration {self.duration:.3f} by more than 0.1s"
            )

        # transition indices reference existing scenes
        n = len(self.scenes)
        for t in self.transitions:
            if t.from_scene >= n or t.to_scene >= n:
                raise ValueError(
                    f"transition references scene index {t.from_scene}→{t.to_scene}, "
                    f"but only {n} scenes exist"
                )

        return self

    def validate_block_props_against(self, registry: "BlockRegistry") -> "SceneGraph":
        """Validate each scene's `block_props` against its BlockSpec's `required_props`.

        Called by Director after construction (schema → registry is a
        one-way dependency, so this lives outside `model_validator`). Returns
        a new SceneGraph with `block_props` canonicalized to the Pydantic
        model's `model_dump()` form so downstream code sees defaults filled in.

        Raises `pydantic.ValidationError` on bad props.
        """
        new_scenes: list[Scene] = []
        for scene in self.scenes:
            spec = registry.resolve(scene.block_id)
            if spec.required_props is not None:
                validated = spec.required_props.model_validate(scene.block_props)
                new_scenes.append(
                    scene.model_copy(update={"block_props": validated.model_dump()})
                )
            else:
                new_scenes.append(scene)
        return self.model_copy(update={"scenes": new_scenes})


# Stable JSON schema hash — provider cache keys incorporate this so a schema
# bump invalidates prior caches automatically.
def compute_schema_hash() -> str:
    schema = SceneGraph.model_json_schema()
    return hashlib.sha256(
        json.dumps(schema, sort_keys=True).encode()
    ).hexdigest()


SCHEMA_HASH: str = compute_schema_hash()
