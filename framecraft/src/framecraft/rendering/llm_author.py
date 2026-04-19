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
from framecraft.schema import BlockId


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


# --- Motion vocabulary (shared between both passes) -----------------------

_MOTION_VOCABULARY = """\
MOTION TECHNIQUE MENU — treat as a buffet. Mix 4–6 per scene, never fewer.

Typography & Text
- Kinetic letters: split headline into <span class="char">; animate y (100→0) + rotation (10°→0) + opacity with stagger 0.03–0.06s using expo.out or back.out(1.7).
- Word stagger: split into <span class="word">; stagger 0.10–0.18s; combine with a slight blur(8px→0) for cinematic feel.
- Variable-weight swell: animate font-variation-settings "wght" from 300→800 (or font-weight stepped) over 0.6s — gives typography kinetic presence without motion.
- Character mask reveal: each char wrapped in an overflow-hidden span; inner span translates y 100%→0% with power3.out.
- Type kerning reveal: letter-spacing 0.4em → 0 with power4.out, 0.9s — headline "collapses into focus".
- Split-flap flip: chars rotate on X-axis (rotateX 90°→0°) with back.out(1.6) and stagger 0.05s (needs transform-style: preserve-3d + perspective on parent).

Reveals & Masking
- Clip-path masked reveal: inset(0 100% 0 0) → inset(0 0 0 0) with power3.out, 0.7–1.0s (horizontal wipe).
- Vertical blind wipe: polygon() clip-path from 0% vertical bands to full, with stagger across 3–5 bands.
- Radial iris: circle(0% at 50% 50%) → circle(150% at 50% 50%) with expo.out, 0.8s.
- Light sweep: a ::before diagonal gradient band that translates from -100% to 200% with power2.out — use to "wipe in" brand color.

Layered atmosphere (REQUIRED — scene MUST have ≥2 of these)
- Grain/noise: a full-bleed <div> with SVG feTurbulence filter OR tiled noise PNG, low opacity 0.05–0.10, optionally micro-rotating 360° over 30s for constant life.
- Radial gradient orbs: 2–3 absolutely-positioned radial gradients (soft blobs), opacity + scale animated independently, filter: blur(40px).
- Backdrop-filter blur pull: a glass layer with backdrop-filter: blur(24px→0px) for a "focus pulls in" reveal.
- Depth-of-field: background element filter: blur(12px) → blur(0px) while foreground does the inverse — camera-rack focus.
- Vignette: radial-gradient overlay that breathes opacity 0.3→0.5 with sine.inOut over scene.
- Scanlines/bands: fixed horizontal line pattern drifting vertically with linear ease (subtle ambient motion).

Structural motion
- Camera push: root div scale 1.0 → 1.05 over full duration with sine.inOut — NEVER skip this.
- Parallax split: foreground drifts 40px one way, background drifts 80px the other way.
- 3D perspective tilt: parent has perspective(1800px); child rotates rotateY(-4°→4°) with sine.inOut over full duration.
- Accent bar draw: width 0 → target with power3.out, 0.6–0.8s.
- Underline streak: SVG stroke-dasharray = path length, stroke-dashoffset tween length→0 with power3.out.
- Geometric wipe: a colored rectangle translates across the canvas, then the foreground appears behind it (transition-as-reveal).

Easing vocabulary
- Entrances: expo.out, back.out(1.4–1.8), power3.out, power4.out, circ.out
- Holds/continuous: sine.inOut, power1.inOut
- Exits: power2.in, power3.in, expo.in
- AVOID: linear (except continuous drifts), default ease, `none`.

Exit (REQUIRED)
In the last 0.4–0.6s every scene must exit visibly: fade + translate, mask closing, or light sweep clearing.
Dead frames before cut = visible stutter in the final video.
"""


_HYPERFRAMES_RULES = """\
HARD HYPERFRAMES RULES (non-negotiable — lint rejects otherwise)

1. Output EXACTLY one element: <template id="{comp_id}-template">…</template>. No prose, no ```fences, no explanatory comments outside the template.
2. Direct child of template MUST be:
   <div data-composition-id="{comp_id}" data-start="0" data-width="{w}" data-height="{h}" data-duration="{duration:.3f}">
3. Every timed element needs class="clip", data-start="X", data-duration="Y", data-track-index="1". Bg/atmospheric layers use start=0, duration={duration:.3f}.
4. Inline <style> must scope every selector with [data-composition-id="{comp_id}"].
5. Load GSAP once: <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>.
6. Timeline (inside one <script>) MUST:
   - set window.__timelines = window.__timelines || {{}};
   - build const tl = gsap.timeline({{ paused: true }});
   - register via window.__timelines["{comp_id}"] = tl;
7. FORBIDDEN: Date.now(), Math.random(), fetch(, setTimeout, setInterval, emoji characters or emoji font families ("Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"), external font URLs, anything non-deterministic. Font stacks: use ONLY `"Inter", sans-serif`, `serif`, or `monospace` — nothing else.
8. Timeline must fill full duration: entry (0→~0.8s), hold with continuous secondary motion, exit (last 0.4–0.6s).
"""


# --- Pass 1: Design Brief -------------------------------------------------

_BRIEF_SYSTEM = """\
You are the motion director for a premium brand video. Before any HTML is written, you draft a short design brief for the scene. Bold, specific, composition-aware.

""" + _MOTION_VOCABULARY + """

Your brief must push beyond the obvious. Centered headline + fade is BANNED. If your first instinct is symmetric + safe, reach for: asymmetric layout, oversized typography, typographic contrast, negative space as a design element, layered atmosphere.

LEGIBILITY RULES (must inform your LAYOUT description):
- Primary copy (headline / tagline / person name / main number) MUST be fully visible inside the canvas during the hold — no permanent off-canvas clipping.
- Oversized type = visually large relative to canvas, NOT pushed off-edge. A multi-word headline that would overflow at a given size must wrap to multiple lines or drop size.
- Only decorative/atmospheric layers (orbs, grain, light sweeps, shapes) may bleed off-canvas.

OUTPUT FORMAT (plain text, NO markdown, NO code fences):

LAYOUT: <2–3 sentences. Where does each element sit? What is dominant? Is it symmetric or asymmetric? What bleeds off-canvas?>
ELEMENTS: <bulleted list of AT LEAST 8 distinct elements you will build. Include ≥2 atmospheric layers (grain, gradient orbs, vignette, scanlines, etc). Every element gets a short CSS-class-ready name.>
  - <name>: <one line — role, position, visual treatment>
  - ... (8+ bullets)
MOTION_MOMENTS: <3 signature motion beats with timing. Name the technique from the menu.>
  1. t=0.0–0.8s: <technique>, <element(s)>
  2. t=X.Xs:     <technique>, <element(s)>
  3. t=exit:     <technique>, <element(s)>
COLOR_DEPTH: <2 sentences. Palette extension beyond props (gradients? accent of accent?). Depth strategy (blur layers? transparency stack? glow?).>
PACING: <one sentence on rhythm — staccato, legato, call-and-response, etc.>

Bias strongly toward visual ambition. No single-fade scenes. No centered-only layouts."""


_BRIEF_USER = """\
Scene brief for your design plan:

Composition id: {comp_id}
Canvas: {w}×{h}  (aspect {aspect})
Duration: {duration:.3f}s
Mood: {mood}
Archetype: {archetype}
Block type: {block_id}
Style directive: {style_seed}

Scene content (block_props):
{props_json}

Draft the design brief now. Be specific. Be bold."""


# --- Pass 2: HTML authoring (brief-in-context) ---------------------------

_AUTHOR_SYSTEM = """\
You are a senior motion designer. You execute a predetermined design brief as a single self-contained Hyperframes <template> block — HTML, scoped CSS, and a GSAP timeline.

""" + _MOTION_VOCABULARY + """

DENSITY FLOOR (enforced — fewer is a fail):
- ≥ 8 animated DOM elements in total
- ≥ 2 atmospheric/background layers (grain, gradient orbs, vignette, blur, scanlines, light sweep, etc.)
- ≥ 10 gsap tween calls (tl.to / tl.from / tl.fromTo) in the timeline
- ≥ 4 distinct motion techniques from the menu
- Every non-atmospheric element must get at least one tween

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

""" + _HYPERFRAMES_RULES + """

OUTPUT
Only the <template>…</template> element. No prose. No ```html fences. No design notes.
"""


_AUTHOR_USER = """\
Execute this design brief as a bespoke Hyperframes <template>.

=== SCENE CONTEXT ===
Composition id: {comp_id}
Canvas: {w}×{h}  (aspect {aspect})
Duration: {duration:.3f}s
Mood: {mood}
Archetype: {archetype}
Style directive: {style_seed}

Scene content (block_props):
{props_json}

=== DESIGN BRIEF TO EXECUTE ===
{brief}

=== REMINDERS ===
- Build EVERY element listed in the brief's ELEMENTS section.
- Land EVERY motion moment from MOTION_MOMENTS.
- Density floor: ≥8 animated elements, ≥10 tweens, ≥4 techniques, ≥2 atmospheric layers.
- Honor the style directive on color, easing, and layout vibes.

Emit only the <template>…</template>. Go."""


# --- Public entry ---------------------------------------------------------


def author_scene_html(provider: LLMProvider, req: AuthorRequest) -> str:
    """Two-pass LLM authoring: design brief → HTML execution.

    Pass 1 forces the LLM to commit to an 8+ element layout, 3 named motion
    moments, and a color/depth strategy before a single line of HTML is
    written. Pass 2 executes that brief with strict density/composition
    rules. Raises LLMAuthorError on any validation failure.
    """
    if isinstance(provider, StubProvider):
        raise LLMAuthorError("stub provider cannot author scenes (determinism)")

    comp_id = scene_id(req.scene_index, req.block_id)
    ctx = _scene_context(req, comp_id)

    # --- Pass 1: design brief
    brief_resp = provider.complete(
        [{"role": "user", "content": _BRIEF_USER.format(**ctx)}],
        system=_BRIEF_SYSTEM,
    )
    brief = brief_resp.text.strip()
    if len(brief) < 200:
        _dump_debug(comp_id + ".brief", brief_resp.text)
        raise LLMAuthorError(
            f"design brief suspiciously short ({len(brief)} chars) — likely a refusal"
        )

    # --- Pass 2: HTML execution
    author_system = _AUTHOR_SYSTEM.format(
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
    except LLMAuthorError:
        _dump_debug(comp_id, resp.text)
        _dump_debug(comp_id + ".brief", brief)
        raise
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
    }


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

_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Date.now", r"Date\.now\("),
    ("Math.random", r"Math\.random\("),
    ("fetch",     r"\bfetch\s*\("),
    ("setTimeout", r"\bsetTimeout\s*\("),
    ("setInterval", r"\bsetInterval\s*\("),
    # Non-deterministic fonts rejected by the Hyperframes compiler.
    ("emoji_font_apple", r"Apple Color Emoji"),
    ("emoji_font_segoe", r"Segoe UI Emoji|Segoe UI Symbol"),
)


def _validate(html: str, comp_id: str) -> None:
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
