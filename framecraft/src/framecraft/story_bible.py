"""Story Bible pass — situation → narrative blueprint.

Runs BEFORE the Director. Produces a full film plan — thesis, weighted
pillars, per-scene copy ledger, data exhibits, motif arc — that everything
downstream consumes. See `schema.StoryBible` for the data model.

The bible is generated in JSON-mode (no `response_schema`) because Vertex AI's
response_schema can't express our pydantic Union/validation setup. We validate
post-hoc and retry once with the error feedback on failure.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from framecraft.providers.base import LLMProvider
from framecraft.providers.stub import StubProvider
from framecraft.schema import (
    Archetype,
    BlockId,
    Brief,
    Scene,
    SceneBrief,
    SceneGraph,
    SceneRole,
    StoryBible,
)


class StoryBibleError(Exception):
    """Bible generation or validation failed after retry."""


# --- Brand system detection (shared with llm_author) ---------------------
#
# Kept here to avoid a circular dep; the llm_author has its own detector that
# uses the same keyword set. Small duplication, big decoupling win.

_BRAND_SYSTEM_AIA_NOTES = """\
BRAND: AIA (Singapore life/health insurance)
- Palette: primary #D31145 (AIA red), rose pink #F5DDE0 / #E9B5BA (cards), cream #FAF4EE (bg), dark ink #1A1A1A (body), orange #E86C29 (chips), beige #E8D9C5 (tape accents).
- Typography: bold sans-serif display, sentence-case headlines, ALL-CAPS eyebrow labels, generous line-height for body.
- Tagline: "Healthier, Longer, Better Lives." on end-card lockups.
- Motifs: first-aid cross, shield outline, heart-with-cross, up-arrow growth, ribbon/chip badges, washi-tape cards.
- Tone: trustworthy, reassuring, Singaporean-professional. Warm but precise.
- Brand facts to use verbatim (pick the ones the film needs — do not invent others):
  * S$2 million annual claim limit
  * Up to 13 months pre- and post-hospitalisation benefits (longest in market)
  * Guaranteed lifetime coverage, unlimited lifetime claim amount
  * Extra S$100,000 for 30 critical illnesses per policy year
  * 380+ AQHP (AIA Quality Healthcare Partner) specialists with 5+ years' experience
  * Zero co-insurance via Deductible Waiver Pass on first private hospital claim
  * Covers congenital abnormalities for the Insured with no waiting time
  * Covers Inpatient Hospice Palliative Care (New!)
  * MediShield Life supplement — Gold Max A + Max VitalHealth A fully covers deductible + co-insurance with AIA Preferred Providers
"""


_BRAND_KEYWORDS: dict[str, str] = {
    "aia": _BRAND_SYSTEM_AIA_NOTES,
}


def _resolve_brand(style_seed: str | None, situation: str) -> str:
    haystack = " ".join(filter(None, [style_seed, situation])).lower()
    for key, spec in _BRAND_KEYWORDS.items():
        if key in haystack:
            return spec
    return ""


# --- Prompts -------------------------------------------------------------

_SYSTEM = """\
You are a senior motion-graphics director and copywriter. You plan entire brand films before a single frame is drawn.

You are about to plan a {duration:g}-second film for this situation:
  "{situation}"
Aspect: {aspect}  (canvas {w}×{h})
Audience & tone should match the brand and the stated style directive.

{brand_notes}

Your output is a STORY BIBLE — a complete narrative blueprint the downstream author will execute. Treat this like a one-page film treatment + shot list.

# HOW TO PLAN
1. **Thesis**: the single sentence a viewer must remember after watching.
2. **Pillars (2–7)**: the weighted key messages that defend the thesis. Mark the 1–2 most important as `tier=hero` with `anchor: true` on the single most-important one. Others are `support` or `context`. Weights 1 (lowest) to 5 (highest). Hero pillars MUST each be carried by at least one scene.
3. **Scenes ({min_scenes}–{max_scenes})**: each scene has a ROLE in the story arc:
     - hook          → open the question, set tone, reveal thesis hint
     - hero-beat     → the anchor / signature moment of the film
     - evidence      → stats/charts/comparisons that prove the pillars
     - proof-point   → a specific claim or detail
     - resolution    → tie the arc, state the payoff
     - brand-lockup  → final frame — brand + tagline + CTA
     - transition    → (sparingly) connective tissue between beats
   Distribute durations so hero scenes get more time. Total duration MUST sum to within ±0.5s of {duration:g}s.
4. **Per scene**:
   - `tier`: hero / support / context — drives how dense the author can go.
   - `tone` (enum): warm-restrained | confident-punch | data-precise | tender-close | energetic-pivot.
   - `carries`: list of pillar ids the scene conveys.
   - `narrative`: one line — what the viewer understands by the end of this scene.
   - `entry_motif` / `exit_motif`: one-line motion handoffs at the scene seam. Scene N's exit_motif should inform scene N+1's entry_motif (match or intentionally contrast).
   - `copy_items`: EVERY text element the scene will render, with exact text and a max_chars budget.
       - Budget rules of thumb (1920×1080): headline max_chars ≤ 32, stat_value ≤ 10, stat_label ≤ 30, subhead ≤ 60, bullet ≤ 60, eyebrow ≤ 28, body ≤ 180, tagline ≤ 48, cta ≤ 36.
       - For 9:16 (vertical), reduce headline max_chars to 22.
       - `kind` values: eyebrow | headline | subhead | body | bullet | stat_value | stat_label | caption | cta | tagline.
   - `exhibit_ids`: references to bible.exhibits that this scene renders.
5. **Exhibits (0–12)**: data objects referenced by scenes. Pick the kind(s) that match the pillars:
     - comparison_table: {{columns: [...], rows: [[...], [...]], highlight_column?: int}}
     - line_curve:       {{x_label, y_label, series: [[x,y], ...], style: "bezier-smooth"|"stepped"|"linear", area_fill: bool, milestone_indices: [int]}}
     - bar_chart:        {{bars: [[label, value_str], ...], unit: str, orientation: "vertical"|"horizontal"}}
     - stat_grid:        {{cells: [{{value, label, chip?}}, ...]}} — 3 to 6 cells
     - checklist:        {{items: [str, ...]}}
     - timeline:         {{milestones: [[time_label, event], ...]}}
   Use REAL numbers from the brand facts when available. Do not invent numbers.
6. **Motif (optional but recommended)**: ONE visual element that progresses across scenes (e.g. a shield drawing in strokes across scenes 2→4→6). Provide a `scene_progression` dict mapping scene-index (as string) to a one-line state at that scene.

# RULES OF CRAFT
- Every scene conveys ONE clear idea. If you're trying to say three things in one scene, make three scenes.
- Hero scenes get longer durations (3.5–7s typical); context scenes are shorter (2–4s).
- Copy is bold, specific, concrete. Use numbers. Use the brand's actual phrases.
- Character budgets are NON-NEGOTIABLE — if a fact is 14 chars but headline max is 12, shorten it; if the headline feels too tight, demote the fact to a sub-line.
- At least ONE scene must be marked `is_anchor: true` (the hero-beat).
- Every scene with `tier: "hero"` MUST list ≥1 pillar id in `carries`. Hero scenes without pillars are invalid.
- The last scene is almost always `role: "brand-lockup"` with a tagline.
- ALL ids (pillars, exhibits, motif, copy_items) must be kebab-or-snake-case without spaces: use `[A-Za-z0-9_\\-]` characters only.
- For `stat_grid` exhibits: every cell is `{{"value": str, "label": str}}` and optionally includes `"chip": str`. NEVER emit `"chip": null` — omit the key entirely if there's no chip.

# OUTPUT FORMAT
Output ONE JSON object matching this shape — no markdown fences, no prose:
{{
  "situation": "...",
  "thesis": "...",
  "audience": "...",
  "overall_tone": "...",
  "brand_keyword": "aia" | null,
  "duration": {duration:g},
  "pillars": [{{"id": "...", "claim": "...", "tier": "hero"|"support"|"context", "weight": 1..5, "anchor": bool}}],
  "exhibits": [
    {{"id": "...", "kind": "...", ...kind-specific fields}}
  ],
  "motif": {{"id": "...", "description": "...", "scene_progression": {{"0": "...", "1": "..."}}}} | null,
  "scenes": [
    {{
      "index": 0,
      "role": "...",
      "duration_s": number,
      "tier": "...",
      "is_anchor": bool,
      "carries": ["pillar-id", ...],
      "tone": "...",
      "entry_motif": "...",
      "exit_motif": "...",
      "copy_items": [{{"id": "snake_case", "text": "...", "kind": "...", "max_chars": number}}],
      "exhibit_ids": ["exhibit-id", ...],
      "narrative": "..."
    }}
  ]
}}

Bias toward ambition. 7-scene arcs with an exhibit and a motif beat timid 3-scene "title, body, end" films every time.
"""


_USER = """\
Write the story bible for this {duration:g}-second film.

SITUATION:
{situation}

STYLE DIRECTIVE:
{style_seed}

Generate the complete JSON now. Remember:
- Character budgets per copy_item matter — do not exceed them.
- Pillars and scenes must cross-reference correctly.
- Total scene duration_s must sum to {duration:g}s ±0.5s.
- Use the brand's actual facts and phrases where applicable.
- At least one scene has `is_anchor: true`.
- Last scene is usually `role: "brand-lockup"`.
"""


# --- Public entry --------------------------------------------------------


def build_bible(
    brief: Brief,
    provider: LLMProvider,
    *,
    min_scenes: int = 4,
    max_scenes: int = 7,
) -> StoryBible:
    """Generate and validate a StoryBible for the given brief.

    Raises StoryBibleError if the model's output still fails validation
    after a retry. Stub providers are unsupported (deterministic bible
    generation would need recorded fixtures — out of scope for now).
    """
    if isinstance(provider, StubProvider):
        raise StoryBibleError(
            "stub provider cannot generate story bibles (no fixture support)"
        )

    brand_notes = _resolve_brand(brief.style_seed, brief.situation)
    w, h = brief.aspect.dimensions

    system = _SYSTEM.format(
        situation=brief.situation,
        duration=brief.duration,
        aspect=brief.aspect.value,
        w=w,
        h=h,
        brand_notes=brand_notes or "(No brand system matched this situation — infer palette/tone from the style directive.)",
        min_scenes=min_scenes,
        max_scenes=max_scenes,
    )
    user = _USER.format(
        situation=brief.situation,
        duration=brief.duration,
        style_seed=brief.style_seed or "(none — use your own editorial judgement)",
    )

    # --- Attempt 1
    resp = provider.complete(
        [{"role": "user", "content": user}],
        system=system,
    )
    bible, err = _parse_and_validate(resp.text)
    if bible is not None:
        return bible

    # --- Attempt 2: feed the error back
    retry_user = (
        user
        + "\n\n=== YOUR PREVIOUS OUTPUT FAILED VALIDATION ===\n"
        + f"Error: {err}\n\n"
        + "Common fixes:\n"
        + "- Pillar ids must match across `pillars` and every `scene.carries` entry.\n"
        + "- `copy_items[].text` length must not exceed its `max_chars`.\n"
        + "- Sum of `scene.duration_s` must be within ±0.5s of the bible `duration`.\n"
        + "- Every hero-tier pillar must be carried by at least one scene.\n"
        + "- Exactly one scene must have `is_anchor: true`.\n"
        + "- Exhibit required fields by kind: comparison_table needs columns+rows, "
        + "line_curve needs x_label+y_label+series, bar_chart needs bars+unit, "
        + "stat_grid needs cells, checklist needs items, timeline needs milestones.\n\n"
        + "Return the CORRECTED complete JSON now."
    )
    resp2 = provider.complete(
        [{"role": "user", "content": retry_user}],
        system=system,
    )
    bible, err2 = _parse_and_validate(resp2.text)
    if bible is not None:
        return bible

    raise StoryBibleError(
        f"bible validation failed after retry.\n"
        f"  attempt 1 error: {err}\n"
        f"  attempt 2 error: {err2}"
    )


# --- Helpers -------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*\n?|\n?```\s*$", re.MULTILINE)


def _parse_and_validate(raw: str) -> tuple[StoryBible | None, str | None]:
    """Return (bible, None) on success or (None, error_msg) on failure."""
    text = _FENCE_RE.sub("", raw).strip()
    # Strip any leading prose before the first '{' (LLM sometimes preambles).
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace == -1 or last_brace == -1:
        return None, f"no JSON object found in response (first 200 chars: {raw[:200]!r})"
    json_text = text[first_brace : last_brace + 1]
    try:
        data: Any = json.loads(json_text)
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    try:
        return StoryBible.model_validate(data), None
    except ValidationError as e:
        return None, f"pydantic validation: {e}"


# --- Bible → SceneGraph conversion ---------------------------------------
#
# With full-polish the LLM author builds each scene from the bible directly,
# so the Director's planning role collapses to a deterministic mapping from
# bible scenes → SceneGraph scenes. This saves an LLM pass AND guarantees
# the plan matches the bible (no drift).


_ROLE_TO_BLOCK: dict[SceneRole, BlockId] = {
    SceneRole.HOOK: BlockId.TITLE_CARD,
    SceneRole.HERO_BEAT: BlockId.TITLE_CARD,
    SceneRole.EVIDENCE: BlockId.LOWER_THIRD,
    SceneRole.PROOF_POINT: BlockId.LOWER_THIRD,
    SceneRole.RESOLUTION: BlockId.TITLE_CARD,
    SceneRole.BRAND_LOCKUP: BlockId.END_CARD,
    SceneRole.TRANSITION: BlockId.GRADIENT_BG,
}


def _first_copy(sb: SceneBrief, kinds: tuple[str, ...], default: str) -> str:
    """Return the text of the first copy_item whose kind matches, else default."""
    for item in sb.copy_items:
        if item.kind in kinds:
            return item.text
    return default


def _props_for_block(sb: SceneBrief, block_id: BlockId) -> dict:
    """Best-effort block_props extraction from the bible's copy_items.

    The LLM author reads the full SceneBrief directly (richer than these
    props). This minimal map only exists so native templates can render a
    sensible fallback if the LLM authoring path fails validation.
    """
    if block_id is BlockId.TITLE_CARD:
        return {
            "headline": _first_copy(sb, ("headline",), sb.narrative[:60])[:120],
            "subtitle": _first_copy(sb, ("subhead", "body"), "")[:180] or None,
        }
    if block_id is BlockId.END_CARD:
        return {
            "tagline": _first_copy(sb, ("tagline", "cta", "headline"), "Thank you.")[:120],
        }
    if block_id is BlockId.LOWER_THIRD:
        return {
            "name": _first_copy(sb, ("headline", "stat_value"), sb.narrative[:40])[:80],
            "role": _first_copy(sb, ("subhead", "stat_label", "body"), "")[:100] or None,
        }
    if block_id is BlockId.GRADIENT_BG:
        return {}
    return {}


def bible_to_plan(bible: StoryBible, brief: Brief) -> SceneGraph:
    """Deterministically convert a StoryBible into a SceneGraph.

    Skips the Director. The resulting plan's scene count, durations, and
    ordering come straight from the bible; block_ids are mapped from role.
    The LLM author consumes the bible (richer than block_props) at render
    time; block_props here only matter for the native fallback path.
    """
    w, h = brief.aspect.dimensions
    t = 0.0
    scenes: list[Scene] = []
    for sb in bible.scenes:
        block_id = _ROLE_TO_BLOCK[sb.role]
        props = {k: v for k, v in _props_for_block(sb, block_id).items() if v is not None}
        scenes.append(
            Scene(
                index=sb.index,
                block_id=block_id,
                start=round(t, 3),
                duration=round(sb.duration_s, 3),
                block_props=props,
            )
        )
        t += sb.duration_s

    # Normalise total duration so validators are happy (±0.1s tolerance).
    total = round(sum(s.duration for s in scenes), 3)

    archetype = brief.archetype or Archetype.PRODUCT_PROMO
    return SceneGraph(
        brief=brief,
        archetype=archetype,
        aspect=brief.aspect,
        canvas=(w, h),
        duration=total,
        scenes=scenes,
        transitions=[],
        brand_kit=brief.brand_kit,
    )


__all__ = ["build_bible", "bible_to_plan", "StoryBibleError"]
