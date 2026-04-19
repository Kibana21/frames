"""LLM-authored full-scene HTML (prototype).

Replaces the static Python `spec.template(...)` output with a single LLM call
that produces bespoke, motion-rich HTML for one scene. Hyperframes contract
(§6.9) is enforced via a strict system prompt + post-response validation.

Only reached when `Assembler(full_polish=True)` AND the provider is real
(non-stub). Stub/dry-run paths always fall back to the native template so
goldens stay deterministic.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from framecraft.providers.base import LLMProvider
from framecraft.providers.stub import StubProvider
from framecraft.rendering.ids import scene_id
from framecraft.schema import BlockId, SceneBrief, StoryBible


class LLMAuthorError(Exception):
    """Authoring failed validation. Caller should fall back to native template."""


@dataclass
class AuthorRequest:
    scene_index: int
    block_id: BlockId
    props: dict
    duration: float
    canvas_w: int
    canvas_h: int
    mood: str | None
    archetype: str
    aspect: str
    style_seed: str | None = None
    # Optional Story Bible context — when present, the author executes the
    # bible's scene brief exactly (copy + exhibits + motif seams) instead of
    # inventing content from thin block_props.
    scene_brief: SceneBrief | None = None
    prev_scene_brief: SceneBrief | None = None
    next_scene_brief: SceneBrief | None = None
    bible: StoryBible | None = None


# --- Motion vocabulary (shared between both passes) -----------------------

_MOTION_VOCABULARY = """\
MOTION TECHNIQUE MENU — treat as a buffet. A polished scene mixes 6+ techniques across 3+ categories.

## Typography (advanced — pick 2+)
- Char-mask reveal: each char in overflow-hidden <span class="char-wrap">; inner <span class="char"> translates y:100%→0% with power3.out, stagger 0.03–0.05s.
- Word stagger with blur: split into <span class="word">; tween y, opacity, AND filter blur(8px→0px), stagger 0.10–0.18s.
- Variable-weight swell: font-variation-settings "wght" 300→900 over 0.7s, OR stepped font-weight swaps via gsap timeline.
- Kerning collapse: letter-spacing 0.5em → normal with power4.out, 1.0s — headline "collapses into focus".
- Split-flap flip: chars rotateX 90°→0° with perspective(1400px) on parent, stagger 0.04s, back.out(1.6).
- Outline-to-fill crossfade: render headline twice — one with -webkit-text-stroke, one solid — crossfade opacity over 0.8s.
- Gradient text fill sweep: background-clip: text with linear-gradient, animate background-position 200%→0% over 1.2s.
- Tracking pulse: continuous letter-spacing oscillation ±0.03em with sine.inOut (subtle breathing on holds).
- Stacked-typography reveal: headline + huge transparent outline duplicate behind it, offset by 8–12px, reveal with staggered mask.
- Kinetic number/count: tween a wrapper's textContent via gsap `onUpdate` + Number coercion — "0 → 2,400".
- Word replacement cascade: word A dissolves char-by-char while word B materializes in the same position (for copy pivots).

## Reveals & Masking (advanced)
- Clip-path horizontal wipe: inset(0 100% 0 0) → inset(0 0 0 0) with power3.out, 0.7–1.0s.
- Vertical blind: clip-path polygon() with 4–5 bands staggered 0.08s.
- Radial iris: clip-path circle(0% at …) → circle(150% at …) with expo.out, 0.8s.
- Light sweep: diagonal gradient band on ::before translates -100% → 200% with power2.out.
- Spotlight tracker: radial-gradient mask follows a subject's position via gsap timeline (moving highlight).
- Ink-stamp SVG reveal: stroke-dasharray = pathLength, stroke-dashoffset tween to 0 with power3.out — hand-drawn feel.
- Diagonal band wipe: clip-path polygon at 30° angle sweeps across (more energetic than axis-aligned).
- Mask-as-transition: clip-path change IS the transition between two visual states within one element.

## Atmospheric layers (REQUIRED — scene MUST have ≥3 of these; atmospheric = decorative, not content)
- feTurbulence grain: SVG filter, opacity 0.05–0.10, slowly micro-rotating 360° over 30s for constant life.
- Radial gradient orbs: 2–3 absolutely-positioned soft blobs, filter: blur(40–80px), independent opacity/scale loops (repeat:-1, yoyo:true).
- Backdrop-filter blur pull: overlay with backdrop-filter: blur(24px→0px) for focus-in.
- Depth-of-field rack: background filter: blur(12px→0px), foreground inverse — camera focus pull.
- Vignette breathe: radial-gradient overlay oscillating opacity with sine.inOut.
- Scanlines: fixed horizontal pattern drifting vertically with linear ease (subtle ambient motion).
- Particle dot grid: 20–50 small dots absolutely positioned; use gsap stagger {{grid: [cols, rows], from: "center"}} for wave entries.
- Mist/haze: soft white radial-gradient drifting diagonally with 20–30s duration, low opacity.
- Light rays: 3–5 thin diagonal lines/gradients sweeping across at different speeds (parallax).
- Chromatic aberration: duplicate edge elements with slight cyan/magenta offsets, opacity 0.1–0.2 — cinema/print feel.
- Film burn flicker: atmospheric opacity oscillation on a warm-tint layer (subtle).
- Noise mesh overlay: SVG noise with mix-blend-mode: overlay for tactile texture across the whole scene.

## Structural / 3D
- Camera push (ALWAYS include): root scale 1.0 → 1.05 over full duration with sine.inOut.
- Parallax split: foreground drifts one direction, background the opposite, for depth.
- 3D perspective tilt: perspective(1800px) on parent, rotateY(-4°→4°) with sine.inOut over full duration.
- Camera dolly: root translate on sine.inOut for "handheld/live-shot" feel.
- Z-axis depth stack: layers with translateZ staggered values (requires transform-style: preserve-3d on parent).
- Orbit motion: child rotates around parent using transform-origin offset.
- Accent bar draw: width 0 → target with power3.out, 0.6–0.8s.
- SVG stroke-draw path: use <path> with stroke-dasharray + stroke-dashoffset animated to 0.
- Geometric wipe transition: colored rectangle translates across revealing content behind.

## Signature / hero moments (REQUIRED — pick EXACTLY 1 per scene, this is the show-stopper)
- Shield assembly: multiple SVG strokes/shapes converging into a brand mark over 1.2s.
- Logo lock-up assembly: components fly in from distinct directions, land with back.out overshoot.
- Stat-card cascade: 3–5 cards with numbers cascade in from depth (z + y) with parallax, back.out(1.4).
- Ripple pulse: concentric circles expanding with scale + fade — continuous heartbeat if health, power pulse if tech.
- Typewriter cascade with cursor: chars appear one at a time, blinking cursor, clears at exit.
- Bar-chart / stat rise: vertical bars scaleY 0→1 with stagger, numbers count up in sync.
- Timeline/roadmap draw: horizontal line with milestone dots popping in sequence, labels follow.
- Spotlight circle reveal: dark overlay with clip-path: circle() that traces a path across subject.
- Icon morph-swap: two icons/symbols alternate via rotateY flip or clip-path morph.
- Mass particle explosion: 40+ elements burst outward from a center point with stagger + ease, fade at edges.
- Hero word swap: huge word A dissolves char-by-char while word B materializes — use for "old way → new way" pivots.
- Ink wash reveal: SVG path with stroke-dasharray draws a large ink-like stroke behind the headline.

## Easing vocabulary
- Entrances: expo.out, back.out(1.4–1.8), power3.out, power4.out, circ.out
- Holds/continuous: sine.inOut, power1.inOut
- Exits: power2.in, power3.in, expo.in
- AVOID: linear (except continuous drifts), default ease, `none`.

## Timeline architecture (REQUIRED)
Flat `tl.to(…)` sequences are weak. Real motion design uses NESTED sub-timelines:

    const headlineTl = gsap.timeline();
    headlineTl
      .from(`${{root}} .char`, {{ y: 100, stagger: 0.04, ease: "back.out(1.7)", duration: 0.8 }})
      .to(`${{root}} .accent-bar`, {{ width: "100%", duration: 0.6, ease: "power3.out" }}, "-=0.3");

    // Continuous ambient — FINITE repeats (scene duration / one cycle).
    const cycle = 3.5;
    const loops = Math.max(0, Math.floor(duration / cycle) - 1);
    const atmosphereTl = gsap.timeline({{ repeat: loops, yoyo: true }});
    atmosphereTl
      .to(`${{root}} .orb-a`, {{ x: "+=60", duration: cycle, ease: "sine.inOut" }}, 0)
      .to(`${{root}} .orb-b`, {{ x: "-=40", duration: cycle, ease: "sine.inOut" }}, 0);

    tl.add(headlineTl, 0.2)
      .add(atmosphereTl, 0)
      .to(`${{root}} .exit-panel`, {{ x: "0%", duration: 0.6, ease: "power3.inOut" }}, duration - 0.6);

- Use ≥2 nested sub-timelines per scene (group related element choreography).
- Timeline has ≥3 phases: entry (0 → ~25% of duration), evolution (~25% → ~75%), exit (last ~15–20%).
- For continuous ambient motion (orbs, vignettes, light sweeps, etc.), use FINITE repeats computed from the scene duration — NEVER `repeat: -1`. Pattern: `repeat: Math.floor(duration / cycle) - 1, yoyo: true` where `cycle` is one animation cycle (e.g. 3 seconds). Infinite repeats break the deterministic frame-capture engine.
- Use gsap.utils.toArray() + stagger {{grid:[cols,rows], from:"center"}} for grid/particle systems.

## Exit (REQUIRED)
In the last 0.4–0.6s every scene must exit visibly: fade + translate, mask closing, light sweep clearing, panel wipe.
Dead static frames before cut = visible stutter in the final video.


## DATA EXHIBIT RENDERING — build these when the brief lists exhibits in scene.exhibit_ids
Each exhibit kind has a preferred visual treatment. Pull real data from the bible's exhibits section.

### comparison_table (columns + rows + highlight_column)
Layout as a horizontal grid of columns (CSS grid-template-columns). Header row = column[i] labels in brand accent. Body rows stagger in from top with clip-path reveal, 0.08s between rows. Highlight column gets a tinted background card and a "winner" chip. 12–20 cells = 12–20 animated elements.
Example skeleton:
  <div class="compare-grid">  (grid, 3 columns)
    <div class="col-head">  (×3) — eyebrow text, brand-red accent underline
    <div class="cell">       (rows × cols cells) — stagger in
    <div class="highlight-panel">  — absolutely-positioned tinted rect behind highlight_column

### line_curve (x_label, y_label, series, style, area_fill, milestone_indices)
Render via SVG. Steps:
 1. Compute path `d` from series: scale [x_min..x_max] → [pad..W-pad] and [y_max..y_min] → [pad..H-pad] (flip y). For style="bezier-smooth", emit a Catmull-Rom → cubic Bézier using two control points per segment. For "linear", just L commands. For "stepped", alternating H/V.
 2. Draw the curve: <path stroke-dasharray="$TOTAL" stroke-dashoffset="$TOTAL"> animated to 0 with power3.out, 1.2–1.6s.
 3. If area_fill: a <path> clone with fill=linear-gradient and opacity tween 0→0.35 after the stroke lands.
 4. Milestone dots at series[milestone_indices[i]] — circles scale 0→1 with back.out(1.8), stagger 0.1s, each with a label that fades in next to it.
 5. Axis labels (x_label bottom-left, y_label rotated -90° on left edge) fade in first, in muted body color.
The draw-in stroke is the canonical "sophisticated curve" motion.

### bar_chart (bars, unit, orientation="vertical"|"horizontal")
Vertical: <div class="bar-col"> for each bar; each has a <div class="bar-fill" style="transform-origin: bottom"> with scaleY 0→(value/max) tweened via power3.out stagger 0.1s. Value label (`${{value}}${{unit}}`) above each bar counts up in sync using gsap onUpdate.
Horizontal: swap scaleX / transform-origin: left; labels at the right end.
Use the brand accent for the tallest (winning) bar, muted for comparisons.

### stat_grid (3–6 cells of {{value, label, chip?}})
CSS grid 3- or 4-column, cells rounded-rectangle cards. Each card:
 1. fromTo scale(0.92) + opacity 0 → 1, stagger 0.12s, back.out(1.6)
 2. Inside each card, the number itself gets its own entry — can be char-mask reveal (split digit-by-digit), variable-weight swell, or count-up via gsap onUpdate.
 3. Small chip badge (if present) appears last on each card with a bounce.
 4. Each card has a short accent bar that draws in beneath its number.
A 4-cell stat grid = 4 cards × 3–4 sub-elements each = 12–16 elements on its own.

### checklist (items)
<ul> with 4–6 rows. Each row has: <svg class="check"> (24–36px brand-color circle with white tick) + <span class="item-text">. Ticks stroke-draw in with stroke-dashoffset, then text slides in from right with power3.out. Stagger 0.14s between rows. Optional: row separator rules that draw in between.

### timeline (milestones: [[time_label, event], …])
Horizontal line (SVG or div) with circle markers at milestone positions. Steps:
 1. Line draws left→right with scaleX 0→1, power3.out, 0.9s.
 2. Circles pop on top at each milestone position with stagger 0.15s, back.out(1.8).
 3. Each milestone's label fades in below/above alternating, slight y-offset.
 4. Active/final milestone gets a pulsing ring accent.

### Construction contract for all exhibits
- Every exhibit's sub-elements get `class="clip"` and appropriate `data-start` / `data-duration` / `data-track-index` (track 5 or 6 works well for exhibit elements to avoid content-track collisions).
- Exhibit entry adds ≥0.8s to the scene — don't try to cram rendering into 0.3s.
- When carrying an exhibit, reduce competing typography density in that scene (the exhibit IS the hero content).
- Animate entry in a single sub-timeline `const exhibitTl = gsap.timeline()` added to main with `tl.add(exhibitTl, <start>)`.
"""


_HYPERFRAMES_RULES = """\
HARD HYPERFRAMES RULES (non-negotiable — lint rejects otherwise)

1. Output EXACTLY one element: <template id="{comp_id}-template">…</template>. No prose, no ```fences, no explanatory comments outside the template.
2. Direct child of template MUST be:
   <div data-composition-id="{comp_id}" data-start="0" data-width="{w}" data-height="{h}" data-duration="{duration:.3f}">
3. Every timed element needs class="clip", data-start="X", data-duration="Y", data-track-index="N". Bg/atmospheric layers use start=0, duration={duration:.3f}.
   TRACK-INDEX STRATEGY (non-negotiable — the validator REJECTS overlapping clips on the same track, and you get ONE retry):
     - RULE: Two `.clip` elements with the same `data-track-index` MUST NOT have overlapping [start, start+duration) intervals. Period.
     - SAFEST APPROACH: give EVERY `.clip` element a UNIQUE `data-track-index`. Indices are integers 1..99 — use as many as you need.
     - Suggested starting buckets, then increment within each bucket:
         1–9   = atmospheric/background layers (orbs → track 8/9, grain → 2, vignette → 10, light sweep → 11)
         10–19 = secondary atmosphere / decorative shapes
         20–29 = primary copy (headline = 20, subtitle = 21, stat_value = 22, stat_label = 23, body = 24)
         30–39 = bullet-list items (bullet 0 = 30, bullet 1 = 31, …)
         40–49 = exhibit elements (each bar, card, milestone gets its own track)
         50    = accent bar / underline
         60    = signature/hero element
         70    = exit panel / transition overlay
     - WRONG (will be rejected — overlap on track 1):
         <div class="clip" data-start="0" data-duration="5" data-track-index="1"></div>
         <div class="clip" data-start="0" data-duration="5" data-track-index="1"></div>
     - RIGHT:
         <div class="clip" data-start="0" data-duration="5" data-track-index="1"></div>
         <div class="clip" data-start="0" data-duration="5" data-track-index="2"></div>
     - Atmospheric layers that all live the full scene duration NEED different track indices (one per layer).
4. Inline <style> must scope every selector with [data-composition-id="{comp_id}"].
   NEVER write a CSS rule that targets the `.clip` class (e.g. `.clip {{ ... }}` or `[data-composition-id="..."] .clip {{ ... }}`).
   `.clip` is the Hyperframes runtime visibility class — it is reserved, and any layout/position/size properties on it will stamp onto every timed element in the scene and break layout (everything collapses to 0,0).
   WRONG:  `[data-composition-id="{comp_id}"] .clip {{ position: absolute; overflow: hidden; }}`
   RIGHT:  use SPECIFIC class names for each element's layout:
           `[data-composition-id="{comp_id}"] .headline {{ position: absolute; top: 120px; left: 100px; font-size: 96px; }}`
           `[data-composition-id="{comp_id}"] .stat-card {{ position: absolute; width: 320px; height: 200px; background: var(--accent-soft); }}`
   Every element with `class="foo clip"` should be positioned via its `foo` rule, never via `.clip`.
5. Load GSAP once: <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>.
6. Timeline (inside one <script>) MUST:
   - set window.__timelines = window.__timelines || {{}};
   - build const tl = gsap.timeline({{ paused: true }});
   - register via window.__timelines["{comp_id}"] = tl;
7. FORBIDDEN: Date.now(), Math.random(), fetch(, setTimeout, setInterval, requestAnimationFrame, performance.now(), emoji characters or emoji font families ("Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"), external font URLs, anything non-deterministic. Font stacks: use ONLY `"Inter", sans-serif`, `serif`, or `monospace` — nothing else.
8. SELECTOR STRING RULES (validator REJECTS template-literal selectors; the Hyperframes CSS parser crashes on them):
   - BAD (will be rejected):
       querySelector(`${{scope}} .foo`)
       gsap.to(`${{root}} .headline`, {{ ... }})
       gsap.utils.toArray(`${{scope}} .char`)
   - GOOD (use hardcoded strings OR string concatenation):
       querySelector('[data-composition-id="{comp_id}"] .foo')
       const scope = '[data-composition-id="{comp_id}"]';
       gsap.to(scope + ' .headline', {{ ... }})
       gsap.utils.toArray(scope + ' .char')
   - NEVER use backticks with ${{...}} inside any selector argument. Template literals anywhere else (e.g. building class names outside selectors) are fine.
9. Timeline must fill full duration: entry (0→~0.8s), hold with continuous secondary motion, exit (last 0.4–0.6s).
"""


# --- Component vocabulary (tables, cards, shapes, icons, data) -----------

_COMPONENT_VOCABULARY = """\
COMPONENT VOCABULARY — a scene is not just headline + subtitle. Mix in these content components to build rich, substantive scenes.

## Cards & callouts
- Benefit card: pink/cream rounded card, 400–600px wide, left-aligned section label (small caps, color-accented), bold claim text, optional supporting body copy and icon. Typical height 260–360px.
- Stat card: bold oversized number (72–160px), small color label below, rounded rectangle bg. 3–5 cards side by side form a stat grid.
- Quote card: large decorative open-quote glyph, italic body, attribution. Left border accent in brand color.
- Chip / badge: small pill-shaped callout with colored bg + bold text ("NEW!", "Longest in market!", "30%"). Use 1–2 per scene max.

## Tables & comparisons
- Two-column comparison ("Without / With", "Before / After"): left column muted/grey, right column on-brand color. Separated by vertical divider. 3–5 rows of aligned text.
- Three-column tiered comparison: shows progression (basic → plus → max). Rightmost column elevated via background tint, border, or scale.
- Feature matrix: rows = features, columns = tiers, cells contain ✓ / — / custom text. Animate tiers/rows in sequence.

## Lists & structured copy
- Checklist with icons: 4–6 rows of (icon + short claim). Icon in brand accent color, claim in dark body. Stagger entry.
- Numbered steps: large accent numerals (1/2/3) in brand color, step title + short description next to each.
- Bullet triad: 3 short stacked claims separated by thin dividers; accent bar draws in between each.

## Data & numerics
- Number counter: animate 0 → target via gsap onUpdate + Number coercion and textContent. Frame with S$ / % / ×.
- Percentage bar: horizontal bar that fills left-to-right to target %; pair with animated label.
- Vertical bar chart: 3–6 bars with scaleY 0→1 stagger; label above each.
- Timeline / roadmap: horizontal line with 3–6 milestone dots; labels pop in sequence.

## Shapes & dividers
- Accent bar: 4–8px thick, brand-color rectangle drawn via width 0 → target. Place under eyebrow labels, between rows, as column separator.
- Decorative rule: 1px horizontal line, muted color, drawn via width or stroke-dashoffset.
- Floating brand shape: abstract SVG polygon or blob in brand color, small, sits in negative space corner, slowly rotates.
- Geometric corner wedge: triangle or curved shape bleeding off one corner of the canvas (atmospheric, decorative).

## Icons
- Inline SVG icons: shield, heart, cross, arrow, checkmark, star, sparkle — built from <path d="..."/>. Use brand color fill or stroke. Keep shapes simple (5–20 path points).
- Icon + label pair: 48–96px SVG icon next to a 24–32px bold label. Icons stagger in with scale/rotate, labels follow.
- Brand logo lockup: render the brand wordmark via plain text (high font-weight) + optional tagline in small caps. This is the end-card hero.

## Composition patterns
- Hero + stat strip: dominant headline top-left, 3-stat row bottom.
- Split panel: left side = copy, right side = illustrated card/chart.
- Eyebrow + hero + body: small caps section label, then 4x bigger headline, then 2–3 bullets or one supporting line.
- Quote frame: hero quote centered with decorative wedges on either side.
"""


# --- Layout zones (collision avoidance) ----------------------------------

_LAYOUT_ZONES = """\
LAYOUT ZONES — prevent text collisions by assigning each primary-content element to ONE named zone. Decorative/atmospheric layers may ignore zones.

Canvas grid (for {w}×{h}):
  Z-TL = top-left     (x:0–40%,   y:0–40%)
  Z-TR = top-right    (x:60–100%, y:0–40%)
  Z-BL = bottom-left  (x:0–40%,   y:60–100%)
  Z-BR = bottom-right (x:60–100%, y:60–100%)
  Z-C  = center       (x:30–70%,  y:30–70%)
  Z-LT = left third   (x:0–35%,   y:0–100%)
  Z-RT = right third  (x:65–100%, y:0–100%)
  Z-TOP = top strip   (x:0–100%,  y:0–30%)
  Z-BOT = bottom strip (x:0–100%, y:70–100%)

Collision rules (non-negotiable):
- Each primary-content element (headline, subtitle, stat card, benefit card, quote, table, checklist, etc.) is pinned to EXACTLY ONE zone.
- No two primary-content elements may share a zone during the hold — the ELEMENTS section of the brief must list the zone for each.
- Zones Z-TL and Z-TR can coexist; Z-LT and Z-RT can coexist; Z-TOP and Z-BOT can coexist. But Z-C overlaps all of Z-TL/Z-TR/Z-BL/Z-BR — don't combine Z-C with any corner zone.
- When placing text, use the zone's x/y bounds as absolute positioning guides. Primary-copy containers must fit entirely inside their zone at rest.
- Decorative/atmospheric layers (orbs, grain, backdrop, vignette, light sweep, bleeding shapes) are outside the zone system — they sit behind and around.

When in doubt: asymmetric layouts (e.g. headline Z-TL, stats Z-BR, accent shape Z-RT) read clearer than centered stacks.
"""


# --- Brand systems -------------------------------------------------------

_BRAND_SYSTEM_AIA = """\
BRAND SYSTEM — AIA (Singapore)

Palette (use these HEX values literally — do NOT invent substitutes):
  Primary red/crimson:  #D31145   — headlines accents, icons, key-benefit callouts, brand lockup
  Dark crimson (hover): #A80E37   — drop shadows on red, press states, depth
  Rose pink (soft):     #F5DDE0   — callout card backgrounds, gentle fills
  Rose pink (deeper):   #E9B5BA   — secondary card fills, chip backgrounds
  Cream / off-white:    #FAF4EE   — page background, breathing room
  Warm beige:           #E8D9C5   — tape/sticker/wedge accents
  Orange callout:       #E86C29   — "NEW!" / "Longest in market!" chips (sparingly)
  Dark ink (body):      #1A1A1A   — body copy, subhead text
  Soft grey (rule):     #D0D0D0   — dividers, subtle borders

Typography voice:
  - Display headlines: bold to extra-bold weight, tight tracking (-0.02em), sentence or title case. Clean sans-serif (Inter is the substitute for AIA's "Everest").
  - Eyebrow / section labels: ALL CAPS, regular or semibold, small (16–22px), brand red or dark ink.
  - Body copy: regular weight, generous line-height (1.4–1.5), dark ink on cream.
  - Numbers: extra bold, oversized (72–160px), brand red on cream OR cream on brand red.
  - Key AIA tagline: "Healthier, Longer, Better Lives." (in small caps, brand red) — use this for end-card lockups.

Visual motifs to draw upon (build via inline SVG):
  - White Latin cross on red square (first-aid iconography)
  - Up-arrow (growth / coverage up)
  - Shield outline (protection)
  - Heart with small + (health)
  - Ribbon / chip badge with label
  - Washi-tape rounded rectangle (cards feel "taped on")

Brand tone:
  - Trustworthy, assured, human. Not flashy. Not jargon-heavy.
  - Claims are confident but always supported (number, benefit name, callout).
  - Hospitality + healthcare: warm but professional.

Brand facts to draw from (use accurately in copy):
  - S$2 million limit per policy year
  - Up to 13 months pre- and post-hospitalisation (longest in market)
  - Guaranteed lifetime coverage, unlimited lifetime claim amount
  - Extra S$100,000 coverage for 30 critical illnesses per policy year
  - 380+ AQHP specialists, 5+ years experience each
  - Zero co-insurance / deductible-waiver pass on first private hospital claim
  - Covers congenital abnormalities, inpatient hospice palliative care

End-card convention:
  - AIA wordmark (bold sans-serif in brand red) + optional mountain-peak mark
  - Tagline "Healthier, Longer, Better Lives." in small caps below
  - Warm cream background, small print disclaimer at very bottom if needed
"""


_BRAND_SYSTEMS: dict[str, str] = {
    "aia": _BRAND_SYSTEM_AIA,
}


def _resolve_brand_system(style_seed: str | None, situation: str | None = None) -> str:
    """Detect a known brand keyword in the seed/situation and return its spec.

    Returns empty string when no brand matches — the prompt then leans on
    the style_seed alone for color/tone direction.
    """
    haystack = " ".join(filter(None, [style_seed, situation])).lower()
    for key, spec in _BRAND_SYSTEMS.items():
        if key in haystack:
            return spec
    return ""


# --- Pass 1: Design Brief -------------------------------------------------

_BRIEF_SYSTEM_HEAD = """\
You are the motion director AND copywriter for a premium brand video. Before any HTML is written, you draft a design brief for the scene. Think like a top-tier editorial motion studio (Buck, Panoply, Man vs Machine) — bold, specific, rich with substance.

"""

_BRIEF_SYSTEM_TAIL = """\

# CONTENT EXPANSION (very important)
The `block_props` you receive is a THIN SEED, not the final content. Your job is to EXPAND it into a rich scene. A scene with only a headline + subtitle is a failure.
- If you get just a headline: invent 3–5 supporting bullets, a stat, an eyebrow label, a decorative icon, a callout chip. Use the brand system + brand facts for source material.
- If you're on a brand scene (AIA, etc.): mine the BRAND FACTS list for specific numbers, names, claims. Use them verbatim.
- Build components, not just text blocks: benefit cards, comparison tables, stat grids, numbered steps, checklists, timelines. Pick from the COMPONENT VOCABULARY.
- A great scene feels like a magazine spread + motion — dense, intentional, substantive. Not a single-line poster.

# LEGIBILITY RULES (must inform your LAYOUT description)
- Primary copy MUST be fully visible inside the canvas during the hold — no permanent off-canvas clipping.
- Oversized type = visually large relative to canvas, NOT pushed off-edge.
- Only decorative/atmospheric layers may bleed off-canvas.
- Every primary-content element is pinned to ONE named zone (Z-TL / Z-TR / Z-BL / Z-BR / Z-C / Z-LT / Z-RT / Z-TOP / Z-BOT). No two primary elements may share a zone.

# PUSH-BEYOND-OBVIOUS
Centered headline + fade = BANNED.
Single-stat hero + nothing else = BANNED.
Avoid: symmetric stacks, timid pastels, safe sans-on-solid-bg, meaningless decorative blobs.
Reach for: asymmetric editorial grids, oversized typography as composition anchor, tables/cards/charts as hero, brand-specific iconography, strong negative space, layered atmosphere.

OUTPUT FORMAT (plain text, NO markdown, NO code fences):

NARRATIVE: <2 sentences — what story does this scene tell inside the larger film? What's the viewer's takeaway?>

LAYOUT: <2–3 sentences. Where does each element sit (name the zones)? What is dominant vs support vs atmospheric? Is it asymmetric? What bleeds off-canvas (decorative only)?>

SIGNATURE_MOMENT: <one sentence naming the ONE hero beat that makes this scene memorable — a named technique from the Signature section of the menu, OR an ambitious combination of 2+ techniques.>

COMPONENTS_USED: <bulleted list of 2–4 components from the COMPONENT VOCABULARY this scene employs, e.g. "stat grid (3 cards)", "two-column comparison table", "checklist with icons", "numbered steps", "brand logo lockup". Every scene uses ≥2 components.>

ELEMENTS: <bulleted list of AT LEAST 20 distinct elements. Include ≥3 atmospheric layers. Every primary-content element names its zone. Every element has a short CSS-class-ready name, role, zone (if applicable), position, visual treatment.>
  - <name>: <role — zone (if primary) — position — visual treatment (size, color, filter, transform origin, etc.)>
  - ... (20+ bullets)

COPY: <the actual text content you invented for each text element, short and specific. Pull numbers and claims from BRAND FACTS when available.>
  - <element-name>: "<exact copy>"

MOTION_MOMENTS: <at least 6 motion beats with timing. Name the technique from the menu. First is the signature. Last is the exit.>
  1. t=0.0–0.6s (SIGNATURE): <technique>, <element(s)>
  2. t=X.Xs:  <technique>, <element(s)>
  3. t=X.Xs:  <technique>, <element(s)>
  4. t=X.Xs:  <technique>, <element(s)>
  5. t=X.Xs:  <technique>, <element(s)>
  6. t=exit:  <technique>, <element(s)>

TIMELINE_STRUCTURE: <name the 2+ nested sub-timelines you will build and what each orchestrates.>
  - <subTimelineName>: <elements it choreographs>
  - <subTimelineName>: <elements it choreographs>

COLOR_DEPTH: <3 sentences. Exact HEX values from the BRAND SYSTEM palette (if present) — do NOT invent. Gradients used. Depth strategy: z-layering, backdrop-filter, blur radii, mix-blend-modes.>

DOMAIN_VOCABULARY: <2–3 visual motifs from the situation's domain you'll lean into (for AIA: first-aid cross, shield, up-arrow, heart+cross, ribbon badge, tape-rounded cards).>

PACING: <one sentence on rhythm — staccato, legato, call-and-response, crescendo-to-beat, slow-burn-then-reveal.>

Every brief is harder to execute than the last. Substance over prettiness."""


def _build_brief_system(brand_system: str, *, w: int, h: int) -> str:
    """Assemble the Pass-1 brief system prompt with optional brand injection."""
    parts = [_BRIEF_SYSTEM_HEAD, _MOTION_VOCABULARY, _COMPONENT_VOCABULARY, _LAYOUT_ZONES]
    if brand_system:
        parts.append(brand_system)
    parts.append(_BRIEF_SYSTEM_TAIL)
    # _LAYOUT_ZONES references {w} and {h}; _AUTHOR_SYSTEM_TAIL also has
    # {comp_id}/{duration} but those aren't used here. Supply safe passthroughs
    # so format() doesn't KeyError on unused placeholders.
    return "".join(parts).format(w=w, h=h, comp_id="<scene>", duration=0.0)


_BRIEF_USER = """\
Scene brief for your design plan:

Composition id: {comp_id}
Canvas: {w}×{h}  (aspect {aspect})
Duration: {duration:.3f}s
Mood: {mood}
Archetype: {archetype}
Block type: {block_id}
Style directive: {style_seed}

Scene content (block_props — thin seed; see Story Bible Context below for authoritative content):
{props_json}

{bible_context}

Draft the design brief now. Be specific. Be bold. If a Story Bible is present, your brief must honor the copy ledger verbatim, schedule the listed exhibits, match the entry_motif from the previous scene, and hand off to the next scene's entry_motif."""


# --- Pass 2: HTML authoring (brief-in-context) ---------------------------

_AUTHOR_SYSTEM_HEAD = """\
You are a senior motion designer at a top editorial studio (think Buck, Panoply, Hornet, Mograph). You execute a predetermined design brief as a single self-contained Hyperframes <template> block — HTML, scoped CSS, and a GSAP timeline. Substance over prettiness; density over decoration.

"""

_AUTHOR_SYSTEM_TAIL = """\

DENSITY FLOOR (enforced — a scene that hits fewer is a quality fail):
- ≥ 20 animated DOM elements in total (count: atmospheric layers + hero content + card bodies + card labels + decorative accents + transition panels)
- ≥ 3 atmospheric layers from the Atmospheric section
- ≥ 2 components from the COMPONENT VOCABULARY (stat grid, comparison table, checklist, stat cards, timeline, numbered steps, quote, etc.)
- ≥ 25 gsap tween calls total (count tl.to/tl.from/tl.fromTo across the main timeline AND inside nested sub-timelines)
- ≥ 6 distinct motion techniques drawn from ≥ 3 different categories of the motion menu
- ≥ 2 nested sub-timelines via `gsap.timeline()` added to the main timeline with `tl.add(subTl, t)` — flat sequences fail this
- EXACTLY 1 named signature/hero moment per scene — the one motion beat that would survive if everything else were cut.
- Primary content timeline has ≥ 3 phases: entry (0 → ~25% duration), evolution (25% → ~75%), exit (last 15–20%).
- Continuous ambient motion on atmospheric layers via FINITE-repeat gsap timelines — the scene is NEVER fully static during the hold.
- Every primary-content element gets ≥ 1 tween; every atmospheric element gets ≥ 1 continuous or breathing tween.
- COPY: every text element uses the exact copy from the brief's COPY section. Do not re-invent or shorten.
- ZONES: every primary-content element sits inside its assigned zone; no two primary elements overlap.

## AMBITION CHECK
Before submitting, mentally re-scan: do I have a hero moment? Are there 3+ atmospheric layers? Are there nested timelines? Is every second of the hold animated somehow (even subtly)? If any answer is no, go again.

Composition rules:
- Asymmetry over symmetry unless the brief explicitly demands centered
- Typographic hierarchy: one dominant element, one support, one accent — different size/weight/color
- Negative space is a design element — don't fill every pixel

Legibility (NON-NEGOTIABLE — these break the video):
- PRIMARY COPY (the element carrying the scene's meaning: headline / tagline / person name / main number) MUST be 100% visible inside the canvas during the hold. If it animates in from off-canvas or exits off-canvas, it still must be fully on-screen for the middle ≥60% of the scene.
- Containers for primary copy use `top`, `left` ≥ 0 in their final resting state. Negative offsets are only allowed on decorative/atmospheric layers (orbs, grain, light sweeps) or during entry/exit tweens via transform, not via the base CSS position.
- For a {w}×{h} canvas, cap primary headline font-size at roughly `h × 0.28` (e.g. ~300px on 1080p). Multi-word headlines that would overflow the canvas width at that size MUST drop to a size that fits, wrap onto multiple lines, or split words across elements. NEVER use `white-space: nowrap` on primary copy wider than the canvas.
- "Bleeding off-edge" from a style directive refers to OVERSIZED but CENTERED placement (letters visually large relative to canvas, composition dense), NOT to permanently clipping the copy. The viewer must be able to read every word of the primary copy.
- Atmospheric/decorative layers (orbs, gradients, grain, light sweeps, geometric shapes) may freely bleed off any edge.

Text-over-shapes rules (text on decorative motifs is a silhouette-clipping trap):
- PROHIBITED in ALL forms — whether SVG `<text>` or HTML `<div>` positioned over the shape:
    placing long copy (>10 chars) inside or over a fixed-bounds decorative motif (shield, ribbon, hexagon, badge, seal, emblem, medal, chip, orb).
- Why: either the SVG viewBox clips it ("GUARANTEED LIFETIME COVE…"), OR the text extends past the shape's fill silhouette and becomes invisible against the page background ("UARANTEED LIFETIME COVERAG" — white letters landing on cream bg where the shield tapers).
- ALLOWED over a shape: short SYMBOLIC labels ≤ 10 chars: "2M", "13mo", "24/7", "∞", "100%", "NEW!", "LIFETIME", "+".
- ALLOWED for phrases: render the phrase as a standalone HTML `<h1>` / `<div>` POSITIONED NEXT TO (below, above, or beside) the motif — not layered on top of it. The shape is the mark; the phrase is the label.
- Forbidden class names when they wrap long copy: `ribbon-text`, `shield-text`/`shield-label`, `badge-text`, `hexagon-text`, `seal-text`, `emblem-text`, `medal-text`, `chip-text`, `logo-text`. The validator rejects these when their content exceeds 10 characters.

Decorative-motif scale (a motif supports the copy — it should not dominate it):
- Hero SVG motifs (shield, logo, icon cluster) should occupy at most ~25% of the canvas width in their final resting state. A shield that fills the center third of a 1920px canvas is fighting the primary copy for attention.
- Exception: if the SCENE'S SIGNATURE MOMENT is "logo/shield lockup reveal" and there is no competing primary text, the motif can fill up to 40%.

Accent bars and underlines (what reads as "intentional" vs "half-drawn"):
- A short accent tick (48–96px) BELOW a headline always reads as half-drawn — no matter how short. The viewer perceives it as an animation captured mid-stroke. DO NOT place short accent bars below headlines or below card text, ever.
- Valid placements for a short accent tick/mark:
    A. INLINE WITH the eyebrow label (left of or above the eyebrow text, not below it) — the classic editorial eyebrow tick.
    B. As a COLUMN RULE between two layout columns.
    C. As a DIVIDER between stacked sections.
- Valid placement for an underline BELOW a headline: `width ≥ 70%` of the headline's visible width. Reads as an underline.
- FORBIDDEN: any short (≤ 30% of headline width) decorative bar placed BELOW or to the RIGHT of primary copy. It will read as unfinished.
- If the brief/bible asks for an "underline" under the headline, make it full width. If you want a tick mark, tuck it next to the eyebrow — don't float it below the headline.

Concentric rings / pulses around a central icon (SHIELDS, LOGOS, BADGES):
- When drawing a decorative ring/pulse/circle around a focal icon (shield, logo, number, photo), the ring MUST share the icon's center point. This is the single most common visual bug in motion graphics.
- ENFORCE by construction: put the icon and the ring INSIDE THE SAME WRAPPER `<div class="icon-stack">` or `<div class="hero-mark">`. That wrapper is position: relative. Both icon and ring get `position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);` — identical centering.
- FORBIDDEN: ring and icon as siblings of a milestone/card with different ancestors or different positioning offsets.
- FORBIDDEN: ring's SVG viewBox wider than the icon's SVG viewBox without matching `cx`/`cy`/`r` math.
- If the ring is an SVG `<circle>`: its `cx` must equal its parent SVG's width/2 and `cy` its height/2. The parent SVG itself is centered via the wrapper.

Symmetric comparison components (tables, two-column "Without / With", "Before / After"):
- Both columns MUST use IDENTICAL structural layout — same child element order, same vertical alignment, same typographic hierarchy. The only differences between columns should be (a) the text content and (b) the highlight styling (winning column tinted / bordered / scaled).
- BANNED: putting the stat number at the TOP of one column and at the BOTTOM of the other (what happened to "S$1,000" above-left vs "S$0" below-right). Viewers perceive this as broken.
- Recommended column template (apply to both columns):
    1. eyebrow label (small caps, accent color)
    2. body explanation (1 line)
    3. stat value (large, dominant)
    4. optional chip / note
- Place the stat value in the same vertical position in both columns. If one column's stat has a chip, the other column either has a chip too (balanced) or an intentionally-empty placeholder of the same height.
- Chips must NOT overlap their stat value — position them ABOVE, BELOW, or INLINE WITH the number; never centered over it.

Scene-to-scene seam (avoid flash/blank frames at cuts):
- The exit motion at the end of a scene should fade content OUT (opacity + slight translate), NOT wipe a solid panel ACROSS the scene to cover it. A full-canvas cover panel right at the cut creates a visible flash/blank between scenes because the next scene starts with its bg visible before content enters.
- Allowed exit motions: content fade + y-shift, mask CLOSE revealing bg (if bg matches next scene's bg), subtle scale-down.
- Forbidden: a full-bleed panel that slides in to cover everything in the last 0.5s. That plus the next scene's entry animation = visible flash.

Stroke-draw animation timing (don't cut the video mid-draw):
- An SVG stroke-dashoffset draw-in animation MUST complete at or before the scene's midpoint — otherwise the frame that ships to the viewer often shows a half-drawn stroke that reads as a bug.
- Start: within the first 0.2–0.6s of the scene. Duration: ≤ 35% of the total scene duration. End: ≤ 50% of scene duration.
- The HOLD phase (from end-of-draw to exit) must show the COMPLETED stroke for at least 40% of the scene.

""" + _HYPERFRAMES_RULES + """

OUTPUT
Only the <template>…</template> element. No prose. No ```html fences. No design notes.
"""


def _build_author_system(brand_system: str, *, comp_id: str, w: int, h: int, duration: float) -> str:
    """Assemble the Pass-2 author system prompt with optional brand injection.

    Brand spec sits between the motion menu and the density rules so both
    conceptual (colors/tone) and executional (rules) parts can reference it.
    """
    parts = [
        _AUTHOR_SYSTEM_HEAD,
        _MOTION_VOCABULARY,
        _COMPONENT_VOCABULARY,
        _LAYOUT_ZONES,
    ]
    if brand_system:
        parts.append(brand_system)
    parts.append(_AUTHOR_SYSTEM_TAIL)
    return "".join(parts).format(comp_id=comp_id, w=w, h=h, duration=duration)


_ELEVATE_SYSTEM = """\
You are a senior motion designer doing a final polish pass on an already-authored Hyperframes scene. Your single job: find the weakest spot and elevate it, ADDING elements and tweens. Do NOT remove anything.

""" + _MOTION_VOCABULARY + """

ELEVATION RULES
- PRESERVE every existing element (every <div>, <span>, <svg>, <path>). No deletions.
- PRESERVE every existing gsap tween. No rewrites that change timing of existing motion.
- ADD: at least 2 new DOM elements, at least 4 new gsap tweens, at least 1 new technique not present in the current HTML.
- ADD one distinctive secondary motion (continuous ambient, signature detail, or chromatic accent) that was missing.
- DO NOT regress: the enhanced scene must score equal-or-higher than the input on element count, tween count, atmospheric layer count.
- Maintain all HARD HYPERFRAMES RULES (the input already satisfies them — don't break the template wrapper, comp_id, clip attrs, GSAP CDN, timeline registration).
- Maintain the LEGIBILITY RULES — primary copy still fully visible during hold, no negative top/left on primary copy containers, no emoji fonts.

OUTPUT
Return ONLY the enhanced <template>…</template>. Complete HTML. No prose. No ```fences. No "here's the updated version" preamble.
"""


_ELEVATE_USER = """\
The following is a first-pass authored scene. Review it, find the weakest/most-generic element, and elevate.

=== SCENE CONTEXT ===
Composition id: {comp_id}
Canvas: {w}×{h}  (aspect {aspect})
Duration: {duration:.3f}s
Style directive: {style_seed}

=== CURRENT HTML (PRESERVE, then ADD) ===
{current_html}

=== TASK ===
1. Identify the weakest spot (generic styling, missing atmospheric layer, under-animated element, missing signature detail, flat transition).
2. Elevate it by ADDING (never removing):
   - ≥2 new DOM elements
   - ≥4 new gsap tweens (may be in a new nested sub-timeline)
   - ≥1 new technique from a category not yet used in the HTML
   - One secondary continuous motion or signature detail that was missing
3. Return the COMPLETE enhanced <template>…</template>. Keep every existing element and tween intact.

Emit only the <template>. Go."""


_AUTHOR_USER = """\
Execute this design brief as a bespoke Hyperframes <template>.

=== SCENE CONTEXT ===
Composition id: {comp_id}
Canvas: {w}×{h}  (aspect {aspect})
Duration: {duration:.3f}s
Mood: {mood}
Archetype: {archetype}
Style directive: {style_seed}

Scene content (block_props — thin seed; see Story Bible Context below):
{props_json}

{bible_context}

=== DESIGN BRIEF TO EXECUTE ===
{brief}

=== REMINDERS ===
- If a Story Bible is present: use the exact COPY LEDGER strings verbatim — do not shorten, extend, or substitute. Render all listed SCENE EXHIBITS using the DATA EXHIBIT RENDERING section of the vocabulary.
- Match the scene's entry_motif to the previous scene's exit_motif; end on the declared exit_motif so the next scene can pick up the thread.
- Build EVERY element listed in the brief's ELEMENTS section (20+ elements).
- Land EVERY motion moment from MOTION_MOMENTS, with the SIGNATURE_MOMENT implemented prominently.
- Build the nested sub-timelines you named in TIMELINE_STRUCTURE using `gsap.timeline()` + `tl.add(subTl, t)`.
- Lean into the DOMAIN_VOCABULARY motifs visually (custom SVG shapes, icon fragments, atmospheric cues).
- Density floor: ≥20 animated elements, ≥25 tweens, ≥6 techniques across ≥3 categories, ≥3 atmospheric layers, ≥2 nested sub-timelines, ≥2 components, EXACTLY 1 hero moment.
- Honor the style directive on color, easing, and layout vibes; honor the BRAND SYSTEM palette if one is present (use exact HEX values).
- No single-fade scenes. No centered-only layouts (unless explicitly required).
- EVERY second of the hold has motion (atmospheric continuous + content evolution).

Emit only the <template>…</template>. Be ambitious. Go."""


# --- Public entry ---------------------------------------------------------


def author_scene_html(provider: LLMProvider, req: AuthorRequest) -> str:
    """Three-pass LLM authoring: design brief → HTML execution → elevation.

    Pass 1 forces the LLM to commit to a 16+ element layout, named signature
    moment, 5+ motion beats, nested timeline structure, domain vocabulary,
    and a color/depth strategy before a single line of HTML is written.
    Pass 2 executes that brief under strict density (≥16 elements, ≥25
    tweens, ≥6 techniques, ≥3 atmospheric layers, ≥2 nested sub-timelines).
    Pass 3 reviews pass-2 output and additively injects ≥2 elements, ≥4
    tweens, and at least one new technique to elevate the weakest spot —
    falling back to pass-2 output if its result fails validation.

    Raises LLMAuthorError on Pass 2 validation failure. Pass 3 is always
    best-effort (never fatal).
    """
    if isinstance(provider, StubProvider):
        raise LLMAuthorError("stub provider cannot author scenes (determinism)")

    comp_id = scene_id(req.scene_index, req.block_id)
    ctx = _scene_context(req, comp_id)

    # Detect brand system from the style seed or situation (for AIA, etc.).
    brand_system = _resolve_brand_system(req.style_seed, req.props.get("situation"))

    # --- Pass 1: design brief (expands sparse props into rich scene)
    brief_resp = provider.complete(
        [{"role": "user", "content": _BRIEF_USER.format(**ctx)}],
        system=_build_brief_system(brand_system, w=req.canvas_w, h=req.canvas_h),
    )
    brief = brief_resp.text.strip()
    if len(brief) < 200:
        _dump_debug(comp_id + ".brief", brief_resp.text)
        raise LLMAuthorError(
            f"design brief suspiciously short ({len(brief)} chars) — likely a refusal"
        )

    # --- Pass 2: HTML execution (with one retry on validation failure)
    author_system = _build_author_system(
        brand_system,
        comp_id=comp_id,
        w=req.canvas_w,
        h=req.canvas_h,
        duration=req.duration,
    )
    # Insert the brief via sentinel so {braces} inside it can't break format().
    author_user = _AUTHOR_USER.format(**ctx, brief="__FC_BRIEF_SENTINEL__").replace(
        "__FC_BRIEF_SENTINEL__", brief
    )
    resp = provider.complete(
        [{"role": "user", "content": author_user}],
        system=author_system,
    )
    try:
        html = _extract_template(resp.text)
        _validate(html, comp_id)
    except LLMAuthorError as first_err:
        _dump_debug(comp_id + ".pass2-attempt1", resp.text)
        # One retry — feed the validation error back so the LLM can fix it.
        retry_user = (
            author_user
            + "\n\n=== PREVIOUS ATTEMPT FAILED VALIDATION ===\n"
            + f"Error: {first_err}\n\n"
            + "Your previous attempt was rejected for the reason above. "
            + "Fix the specific violation (e.g. if forbidden pattern: remove it; "
            + "if missing: add it; if overlapping clips: re-assign track indices). "
            + "Return the COMPLETE corrected <template>. Do not shrink it."
        )
        retry_resp = provider.complete(
            [{"role": "user", "content": retry_user}],
            system=author_system,
        )
        try:
            html = _extract_template(retry_resp.text)
            _validate(html, comp_id)
        except LLMAuthorError:
            _dump_debug(comp_id + ".pass2-attempt2", retry_resp.text)
            _dump_debug(comp_id + ".brief", brief)
            raise

    # --- Pass 3: elevation (best-effort; falls back to pass-2 HTML on failure)
    try:
        elevate_user = _ELEVATE_USER.format(
            **ctx, current_html="__FC_CURRENT_HTML__"
        ).replace("__FC_CURRENT_HTML__", html)
        elevated = provider.complete(
            [{"role": "user", "content": elevate_user}],
            system=_ELEVATE_SYSTEM.format(),
        )
        enhanced = _extract_template(elevated.text)
        _validate(enhanced, comp_id)
        # Sanity: enhanced must be strictly richer. If it shrank, discard.
        if len(enhanced) < len(html) * 0.95:
            _dump_debug(comp_id + ".elevated-rejected", elevated.text)
            return html
        return enhanced
    except LLMAuthorError as e:
        # Elevation is optional — log and keep the pass-2 result.
        _dump_debug(comp_id + ".elevated-error", str(e))
        return html


def _scene_context(req: AuthorRequest, comp_id: str) -> dict[str, str]:
    """Shared context dict fed into both pass prompts."""
    return {
        "comp_id": comp_id,
        "w": req.canvas_w,
        "h": req.canvas_h,
        "aspect": req.aspect,
        "duration": req.duration,
        "mood": req.mood or "unspecified",
        "archetype": req.archetype,
        "block_id": req.block_id.value,
        "style_seed": req.style_seed or "none — use your own creative judgement",
        "props_json": json.dumps(req.props, indent=2, ensure_ascii=False),
        "bible_context": _format_bible_context(req),
    }


def _format_bible_context(req: AuthorRequest) -> str:
    """Render the Story Bible context for this scene, if available.

    Returns a freeform text block the LLM can read directly. Includes:
    - Full film thesis + pillars (so the scene knows its place in the arc).
    - THIS scene's full brief (copy ledger, exhibits, tone, motifs, role).
    - Previous and next scene's exit/entry motifs for seam matching.
    - Referenced exhibits expanded with their full data.
    Falls back to a "no bible — expand from props" notice if absent.
    """
    if req.scene_brief is None or req.bible is None:
        return (
            "(No Story Bible for this run — invent rich content from the block_props + style_seed. "
            "Treat block_props as a seed, not the final copy; expand into a full scene with "
            "supporting bullets, stats, and visual components per the COMPONENT VOCABULARY.)"
        )

    sb = req.scene_brief
    bible = req.bible

    pillars_block = "\n".join(
        f"  - [{p.tier.value.upper():7}] {p.id} (weight={p.weight}"
        + (", ANCHOR" if p.anchor else "")
        + f"): {p.claim}"
        for p in bible.pillars
    )

    copy_block = "\n".join(
        f"  - {c.id} ({c.kind}, max {c.max_chars or '—'} chars): {c.text!r}"
        for c in sb.copy_items
    ) or "  (no copy_items — this scene is atmospheric / exhibit-led)"

    exhibits_block = ""
    if sb.exhibit_ids:
        parts: list[str] = []
        for eid in sb.exhibit_ids:
            e = bible.exhibit_by_id(eid)
            if e is None:
                continue
            parts.append(
                f"  - {e.id} (kind={e.kind}):\n      "
                + json.dumps(e.model_dump(exclude_none=True), indent=2, ensure_ascii=False)
                .replace("\n", "\n      ")
            )
        exhibits_block = "\n" + "\n".join(parts) if parts else ""

    motif_block = ""
    if bible.motif is not None:
        state_here = bible.motif.scene_progression.get(str(sb.index))
        motif_block = (
            f"\n\nMOTIF ARC (persists across scenes):\n"
            f"  id: {bible.motif.id}\n"
            f"  description: {bible.motif.description}\n"
            f"  state at THIS scene: {state_here or '(no explicit state — inherit from neighbors)'}"
        )

    prev_block = (
        f"  Previous scene (#{req.prev_scene_brief.index}, {req.prev_scene_brief.role.value}): "
        f"exit_motif = {req.prev_scene_brief.exit_motif!r}"
        if req.prev_scene_brief else "  (this is the FIRST scene — cold open)"
    )
    next_block = (
        f"  Next scene (#{req.next_scene_brief.index}, {req.next_scene_brief.role.value}): "
        f"entry_motif = {req.next_scene_brief.entry_motif!r}"
        if req.next_scene_brief else "  (this is the LAST scene — final frame)"
    )

    return f"""\
=== STORY BIBLE CONTEXT (authoritative — execute this, do not re-invent) ===

FILM THESIS: {bible.thesis}
AUDIENCE: {bible.audience}
OVERALL TONE: {bible.overall_tone}

PILLARS:
{pillars_block}

THIS SCENE (#{sb.index} — {sb.role.value}, tier={sb.tier.value}{", ANCHOR" if sb.is_anchor else ""}):
  narrative:   {sb.narrative}
  tone:        {sb.tone.value}
  entry_motif: {sb.entry_motif}
  exit_motif:  {sb.exit_motif}
  carries:     {sb.carries or "(no pillars — connective tissue)"}

SCENE COPY LEDGER (render every text element using these exact strings — do not shorten, extend, or substitute):
{copy_block}

SCENE EXHIBITS (render these with their full data using the DATA EXHIBIT RENDERING section of the menu):{exhibits_block or " (none)"}{motif_block}

SCENE SEAMS (match motion at the cut):
{prev_block}
{next_block}

=== END BIBLE CONTEXT ==="""


# --- Helpers --------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:html|HTML)?\s*\n?|\n?```\s*$", re.MULTILINE)
_TEMPLATE_RE = re.compile(r"<template\b[^>]*>.*?</template>", re.DOTALL | re.IGNORECASE)


def _extract_template(raw: str) -> str:
    """Strip markdown fences and extract the first <template>…</template>."""
    s = _FENCE_RE.sub("", raw).strip()
    m = _TEMPLATE_RE.search(s)
    if not m:
        raise LLMAuthorError(
            f"response contained no <template> element. First 200 chars: {raw[:200]!r}"
        )
    return m.group(0).rstrip() + "\n"


_REQUIRED_PATTERNS: tuple[tuple[str, str], ...] = (
    ("template_open", r"<template\s+id=[\"'][^\"']+-template[\"']"),
    ("comp_div", r"data-composition-id=[\"']{comp_id}[\"']"),
    ("canvas_w", r"data-width=[\"']\d+[\"']"),
    ("canvas_h", r"data-height=[\"']\d+[\"']"),
    ("duration", r"data-duration=[\"']"),
    ("gsap_cdn", r"cdn\.jsdelivr\.net/npm/gsap"),
    ("timelines_global", r"window\.__timelines"),
    ("timeline_paused", r"gsap\.timeline\(\s*\{\s*paused:\s*true"),
    # Registration: window.__timelines[<expr>] = … — accept string literal
    # or a variable; comp_id presence is already guaranteed by comp_div.
    ("timeline_register", r"window\.__timelines\s*\[[^\]]+\]\s*="),
)

_SHAPE_TEXT_KEYWORDS = (
    "ribbon-text", "ribbon-label",
    "shield-text", "shield-label", "shield-caption",
    "badge-text", "badge-label",
    "hexagon-text", "hexagon-label",
    "seal-text", "seal-label",
    "emblem-text", "emblem-label",
    "medal-text", "medal-label",
    "chip-text", "chip-label",
    "logo-text", "logo-label",
)

# One regex per keyword avoids greedy-nesting issues. We match an opening
# tag carrying that exact class token, then capture the text that follows
# until the next `<` — sufficient to detect an overlong label even if the
# element contains further nested markup.
_SHAPE_LABEL_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        kw,
        re.compile(
            rf'<\w+\b[^>]*class=["\'][^"\']*\b{re.escape(kw)}\b[^"\']*["\'][^>]*>([^<]*)',
            re.IGNORECASE,
        ),
    )
    for kw in _SHAPE_TEXT_KEYWORDS
)


def _check_full_bleed_exit_panel(html: str) -> str | None:
    """Reject full-bleed cover panels that wipe in as exit motion.

    A full-canvas panel sliding in during the last 0.5s covers everything,
    then the next scene starts with its own bg visible — that gap is the
    "white flash between scenes" bug. Exit motion should fade/translate
    content OUT instead of covering it IN.

    Heuristic: an element whose class contains 'exit-panel' / 'cover-panel'
    / 'wipe-panel' is almost always a full-bleed cover. Reject it; push
    the LLM toward a content fade.
    """
    pat = re.compile(
        r'<\w+\b[^>]*class=["\'][^"\']*\b('
        r'exit-panel|cover-panel|wipe-panel|scene-cover|full-wipe'
        r')\b',
        re.IGNORECASE,
    )
    for m in pat.finditer(html):
        return (
            f"scene contains a full-bleed exit cover element (class={m.group(1)!r}). "
            "A solid panel that wipes across the scene at the cut creates a visible flash "
            "between scenes. Replace with a content fade+translate exit: "
            "`tl.to(scope + ' .content', { opacity: 0, y: -20, duration: 0.5, ease: 'power2.in' }, duration - 0.5)` "
            "— or a mask close matching the next scene's entry."
        )
    return None


def _check_long_text_in_svg(html: str) -> str | None:
    """Reject long text placed inside or over decorative shapes.

    Two failure modes handled:
    1. SVG `<text>…</text>` — gets clipped by the SVG's fixed viewBox.
    2. HTML `<div class="ribbon-text|shield-label|badge-text|…">…</div>` —
       absolutely positioned over a decorative shape. When the text is
       longer than the shape's narrowest silhouette, the letters extend
       past the shape's fill and become invisible against the canvas bg
       (the "UARANTEED LIFETIME COVERAG" bug).

    Primary copy belongs in standalone HTML DOM text elements adjacent
    to the motif, never layered over/inside it.
    """
    # Case 1: SVG <text> nodes with long phrases.
    for m in re.finditer(r"<text\b[^>]*>(.*?)</text>", html, re.DOTALL | re.IGNORECASE):
        inner = re.sub(r"\s+", " ", m.group(1)).strip()
        inner = re.sub(r"<[^>]+>", "", inner).strip()
        if len(inner) > 12:
            return (
                f"<text> element contains long copy ({len(inner)} chars: {inner[:40]!r}). "
                "Primary copy inside SVG text nodes gets clipped by the viewBox. "
                "Move the text to an HTML DOM element (e.g. <div> or <h1>) next to the SVG "
                "motif — keep SVG <text> for short symbolic labels (≤12 chars) only."
            )

    # Case 2: HTML elements whose class name names them as shape-overlay text.
    for keyword, pattern in _SHAPE_LABEL_RES:
        for m in pattern.finditer(html):
            inner = re.sub(r"\s+", " ", m.group(1)).strip()
            if len(inner) > 10:
                return (
                    f"element with class containing {keyword!r} carries long copy "
                    f"({len(inner)} chars: {inner[:40]!r}) positioned over a decorative shape. "
                    "Text on shapes like shields, ribbons, hexagons, badges and seals gets clipped "
                    "where the shape's silhouette tapers (white text on cream bg = invisible). "
                    "Move the copy to a standalone text element placed below or beside the motif, "
                    "not inside/over it. Use ≤10 chars only for labels that must sit on the shape."
                )
    return None


_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Date.now", r"Date\.now\("),
    ("Math.random", r"Math\.random\("),
    ("fetch",     r"\bfetch\s*\("),
    ("setTimeout", r"\bsetTimeout\s*\("),
    ("setInterval", r"\bsetInterval\s*\("),
    # Non-deterministic animation hooks — Hyperframes capture engine can't seek them.
    ("requestAnimationFrame", r"\brequestAnimationFrame\s*\("),
    ("performance.now", r"\bperformance\.now\s*\("),
    # Non-deterministic fonts rejected by the Hyperframes compiler.
    ("emoji_font_apple", r"Apple Color Emoji"),
    ("emoji_font_segoe", r"Segoe UI Emoji|Segoe UI Symbol"),
    # Infinite GSAP repeats break deterministic frame capture.
    ("gsap_infinite_repeat", r"repeat\s*:\s*-\s*1\b"),
    # Template-literal selectors crash the Hyperframes CSS parser.
    # Caught patterns: `${...}` inside querySelector, gsap.to/from/fromTo,
    # gsap.utils.toArray, or any "scope +"/backticked selector string.
    ("template_literal_selector",
     r"(?:querySelector|querySelectorAll|gsap\.(?:to|from|fromTo|set|utils\.toArray))\s*\(\s*`[^`]*\$\{"),
    # `.clip` is the Hyperframes runtime visibility class. CSS rules that
    # target `.clip` directly stamp layout properties onto EVERY timed
    # element in the scene, clobbering each element's intended position.
    # This makes scenes render blank (every element absolute-positioned at 0,0).
    ("clip_class_styled",
     r"\.clip\s*\{"),
)


def _check_track_overlaps(html: str) -> str | None:
    """Parse .clip elements and verify no two share (data-track-index, overlapping time).

    Returns an error message on violation, or None if clean.
    """
    # Match any tag that contains class="…clip…" and the three data-* attrs.
    # Robust enough for the LLM's output (quoted attribute values, any order).
    tag_re = re.compile(
        r"<\w[^>]*?"
        r"(?=[^>]*class=\"[^\"]*\bclip\b)"
        r"(?=[^>]*data-start=\"([\d.]+)\")"
        r"(?=[^>]*data-duration=\"([\d.]+)\")"
        r"(?=[^>]*data-track-index=\"(\d+)\")"
        r"[^>]*>",
        re.IGNORECASE,
    )
    intervals: dict[int, list[tuple[float, float, int]]] = {}
    for idx, m in enumerate(tag_re.finditer(html)):
        start = float(m.group(1))
        duration = float(m.group(2))
        track = int(m.group(3))
        intervals.setdefault(track, []).append((start, start + duration, idx))

    EPS = 0.001
    for track, clips in intervals.items():
        if len(clips) < 2:
            continue
        clips.sort()
        for (s1, e1, i1), (s2, e2, i2) in zip(clips, clips[1:]):
            if s2 + EPS < e1:
                return (
                    f"overlapping clips on track {track}: clip #{i1} [{s1:.2f}s→{e1:.2f}s] "
                    f"overlaps with clip #{i2} [{s2:.2f}s→{e2:.2f}s]. "
                    f"Assign them different data-track-index values or adjust start/duration."
                )
    return None


def _validate(html: str, comp_id: str) -> None:
    overlap_err = _check_track_overlaps(html)
    if overlap_err:
        raise LLMAuthorError(overlap_err)
    svg_text_err = _check_long_text_in_svg(html)
    if svg_text_err:
        raise LLMAuthorError(svg_text_err)
    exit_err = _check_full_bleed_exit_panel(html)
    if exit_err:
        raise LLMAuthorError(exit_err)
    for label, raw_pat in _REQUIRED_PATTERNS:
        pat = raw_pat.replace("{comp_id}", re.escape(comp_id))
        if not re.search(pat, html):
            raise LLMAuthorError(f"missing required pattern: {label} ({pat})")
    for label, pat in _FORBIDDEN_PATTERNS:
        if re.search(pat, html):
            raise LLMAuthorError(f"forbidden pattern present: {label}")
    if html.count("<template") != 1 or html.count("</template>") != 1:
        raise LLMAuthorError("expected exactly one <template>…</template>")


def _dump_debug(comp_id: str, raw: str) -> None:
    """Save the raw LLM response for inspection on validation failure."""
    debug_dir = Path(os.environ.get("FRAMECRAFT_DEBUG_DIR", tempfile.gettempdir())) / "framecraft-author-debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / f"{comp_id}.html").write_text(raw, encoding="utf-8")


__all__ = ["AuthorRequest", "LLMAuthorError", "author_scene_html"]
