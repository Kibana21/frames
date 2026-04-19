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


# --- Story Bible ----------------------------------------------------------
#
# Narrative-first planning: the Story Bible pass runs BEFORE the Director and
# produces a full film plan — thesis, weighted pillars, per-scene copy ledger,
# data exhibits, motif arc. The Director then derives block_ids/durations from
# the bible, and the LLM author executes each scene against its bible entry
# (plus neighbor context).
#
# All models use plain pydantic validators (no Union/discriminator tricks)
# because Vertex AI's response_schema can't handle anyOf/oneOf. The bible is
# generated in JSON-mode and validated post-hoc.


class Tier(StrEnum):
    HERO = "hero"
    SUPPORT = "support"
    CONTEXT = "context"


class SceneRole(StrEnum):
    HOOK = "hook"
    HERO_BEAT = "hero-beat"
    EVIDENCE = "evidence"
    PROOF_POINT = "proof-point"
    RESOLUTION = "resolution"
    BRAND_LOCKUP = "brand-lockup"
    TRANSITION = "transition"


class Tone(StrEnum):
    WARM_RESTRAINED = "warm-restrained"
    CONFIDENT_PUNCH = "confident-punch"
    DATA_PRECISE = "data-precise"
    TENDER_CLOSE = "tender-close"
    ENERGETIC_PIVOT = "energetic-pivot"


_ID_PATTERN = r"^[A-Za-z0-9_\-]+$"


class Pillar(BaseModel):
    """A weighted key message the film builds around."""
    id: str = Field(pattern=_ID_PATTERN, max_length=40)
    claim: str = Field(min_length=3, max_length=140)
    tier: Tier
    weight: int = Field(ge=1, le=5)
    anchor: bool = False


class CopyEntry(BaseModel):
    """One text element with a character budget derived from its zone."""
    id: str = Field(pattern=_ID_PATTERN, max_length=60)
    text: str = Field(min_length=1)
    max_chars: int | None = Field(default=None, ge=1, le=400)
    kind: Literal["eyebrow", "headline", "subhead", "body", "bullet", "stat_value", "stat_label", "caption", "cta", "tagline"] = "body"

    @model_validator(mode="after")
    def _within_budget(self) -> CopyEntry:
        # Allow up to +15% overshoot before failing — the LLM rarely lands
        # exactly on the budget and tiny overruns don't break layouts. The
        # stricter max_chars is still propagated to the author prompt so
        # the author can size the container to accommodate.
        if self.max_chars is not None:
            ceiling = int(self.max_chars * 1.15) + 1
            if len(self.text) > ceiling:
                raise ValueError(
                    f"copy entry {self.id!r} is {len(self.text)} chars, "
                    f"exceeds max_chars={self.max_chars} by more than 15%"
                )
        return self


# --- Exhibits --------------------------------------------------------------
#
# Exhibits are structured data objects referenced by scenes. Each has an `id`,
# a `kind` discriminator (instead of Union), and fields specific to its kind.
# Unused fields are left as defaults. The bible validator enforces per-kind
# required fields via a post_validate step.


class Exhibit(BaseModel):
    """Unified exhibit model. `kind` selects which fields are required.

    Avoids discriminated unions (Vertex response_schema is strict) — the bible
    validator checks kind-specific requirements after model_validate.
    """
    id: str = Field(pattern=_ID_PATTERN, max_length=60)
    kind: Literal[
        "comparison_table",
        "line_curve",
        "bar_chart",
        "stat_grid",
        "checklist",
        "timeline",
    ]

    # comparison_table
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    highlight_column: int | None = None

    # line_curve
    x_label: str | None = None
    y_label: str | None = None
    series: list[list[float]] | None = None  # [[x, y], …]
    style: Literal["bezier-smooth", "stepped", "linear"] | None = None
    area_fill: bool | None = None
    milestone_indices: list[int] | None = None

    # bar_chart
    bars: list[list[str]] | None = None  # [[label, value_str], …]
    unit: str | None = None
    orientation: Literal["vertical", "horizontal"] | None = None

    # stat_grid — cells are {value, label, chip?}; allow None/missing chip
    cells: list[dict[str, str | None]] | None = None

    # checklist
    items: list[str] | None = None

    # timeline
    milestones: list[list[str]] | None = None  # [[time_label, event], …]

    @model_validator(mode="after")
    def _kind_required(self) -> Exhibit:
        required_by_kind: dict[str, list[str]] = {
            "comparison_table": ["columns", "rows"],
            "line_curve":       ["x_label", "y_label", "series"],
            "bar_chart":        ["bars", "unit"],
            "stat_grid":        ["cells"],
            "checklist":        ["items"],
            "timeline":         ["milestones"],
        }
        missing = [f for f in required_by_kind[self.kind] if getattr(self, f) in (None, [])]
        if missing:
            raise ValueError(f"exhibit {self.id!r} (kind={self.kind}) missing fields: {missing}")
        return self


class MotifArc(BaseModel):
    """A visual motif that persists across scenes and progresses through the film."""
    id: str = Field(pattern=_ID_PATTERN, max_length=40)
    description: str = Field(min_length=3, max_length=200)
    # One progression entry per scene index the motif appears in.
    # Key = scene_index (as str), value = short description of motif state at that scene.
    scene_progression: dict[str, str] = Field(default_factory=dict)


class SceneBrief(BaseModel):
    """The narrative contract for one scene — what it says, how, and why."""
    index: int = Field(ge=0)
    role: SceneRole
    duration_s: float = Field(gt=0)
    tier: Tier
    is_anchor: bool = False
    carries: list[str] = Field(default_factory=list)  # pillar ids
    tone: Tone
    # One-line motion description at scene seam (author must match these across neighbors).
    entry_motif: str = Field(min_length=3, max_length=200)
    exit_motif: str = Field(min_length=3, max_length=200)
    copy_items: list[CopyEntry] = Field(default_factory=list)
    exhibit_ids: list[str] = Field(default_factory=list)
    narrative: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def _tier_density_floor(self) -> SceneBrief:
        # hero scenes MUST carry at least one pillar
        if self.tier == Tier.HERO and not self.carries:
            raise ValueError(
                f"scene {self.index} is tier=hero but carries no pillars"
            )
        return self


class StoryBible(BaseModel):
    """Narrative blueprint for an entire film — produced before the Director."""
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    situation: str = Field(min_length=3, max_length=2000)
    thesis: str = Field(min_length=10, max_length=280)
    audience: str = Field(min_length=3, max_length=200)
    overall_tone: str = Field(min_length=3, max_length=200)
    brand_keyword: str | None = Field(default=None, max_length=40)
    duration: float = Field(gt=0)
    pillars: list[Pillar] = Field(min_length=2, max_length=7)
    exhibits: list[Exhibit] = Field(default_factory=list, max_length=12)
    motif: MotifArc | None = None
    scenes: list[SceneBrief] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def _cross_refs(self) -> StoryBible:
        # Every carried pillar must exist
        pillar_ids = {p.id for p in self.pillars}
        for s in self.scenes:
            bad = [pid for pid in s.carries if pid not in pillar_ids]
            if bad:
                raise ValueError(
                    f"scene {s.index} carries unknown pillars: {bad} (valid: {sorted(pillar_ids)})"
                )

        # Every referenced exhibit must exist
        exhibit_ids = {e.id for e in self.exhibits}
        for s in self.scenes:
            bad = [eid for eid in s.exhibit_ids if eid not in exhibit_ids]
            if bad:
                raise ValueError(
                    f"scene {s.index} references unknown exhibits: {bad} (valid: {sorted(exhibit_ids)})"
                )

        # Every hero-tier pillar must appear in at least one scene
        carried = {pid for s in self.scenes for pid in s.carries}
        orphan_hero = [p.id for p in self.pillars if p.tier == Tier.HERO and p.id not in carried]
        if orphan_hero:
            raise ValueError(
                f"hero pillars not carried by any scene: {orphan_hero}"
            )

        # Scene indices are contiguous from 0
        expected = list(range(len(self.scenes)))
        got = [s.index for s in self.scenes]
        if got != expected:
            raise ValueError(f"scene.index must be contiguous from 0; got {got}")

        # Duration sum within ±0.5s of bible duration (bible is coarser than SceneGraph)
        total = sum(s.duration_s for s in self.scenes)
        if abs(total - self.duration) > 0.5:
            raise ValueError(
                f"sum(scene.duration_s) = {total:.2f} differs from bible duration "
                f"{self.duration:.2f} by more than 0.5s"
            )

        # At least one scene must be an anchor
        if not any(s.is_anchor for s in self.scenes):
            raise ValueError("at least one scene must have is_anchor=True")

        return self

    def scene_by_index(self, idx: int) -> SceneBrief | None:
        for s in self.scenes:
            if s.index == idx:
                return s
        return None

    def exhibit_by_id(self, eid: str) -> Exhibit | None:
        for e in self.exhibits:
            if e.id == eid:
                return e
        return None


# Stable JSON schema hash — provider cache keys incorporate this so a schema
# bump invalidates prior caches automatically.
def compute_schema_hash() -> str:
    schema = SceneGraph.model_json_schema()
    return hashlib.sha256(
        json.dumps(schema, sort_keys=True).encode()
    ).hexdigest()


SCHEMA_HASH: str = compute_schema_hash()
