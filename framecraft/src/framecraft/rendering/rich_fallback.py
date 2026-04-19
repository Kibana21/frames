"""Rich-safe fallback HTML for scenes where the LLM author failed validation.

When both Pass-2 authoring attempts fail lint/validation, we previously fell
back to the plain Python template (one opacity fade — visibly static). That
breaks the film's rhythm and the user-visible bug was "scenes 4-5 look flat."

This module builds a deterministic HTML composition from the bible's
`SceneBrief`: eyebrow + headline + body + bulleted claims + accent bar +
atmospheric gradient + grain + exit wipe, with staggered GSAP entry, a slow
camera push through the hold, and a clean exit. Always passes Hyperframes
lint (finite repeats, non-overlapping tracks, no emoji fonts, no rAF).

Honors brand palette when `AIA` is detected in the style_seed; otherwise
neutral dark-ink-on-cream.
"""

from __future__ import annotations

from framecraft.rendering.ids import scene_id
from framecraft.schema import BlockId, SceneBrief

# Palette presets — kept minimal, only the colors we actually render.
_PALETTES: dict[str, dict[str, str]] = {
    "aia": {
        "bg":       "#FAF4EE",
        "ink":      "#1A1A1A",
        "accent":   "#D31145",
        "accent_soft": "#F5DDE0",
        "muted":    "#6A6A6A",
        "rule":     "#D0D0D0",
    },
    "default": {
        "bg":       "#0A0A0A",
        "ink":      "#FFFFFF",
        "accent":   "#E5E5E5",
        "accent_soft": "#222222",
        "muted":    "#A8A8A8",
        "rule":     "#303030",
    },
}


def _pick_palette(style_seed: str | None) -> dict[str, str]:
    if style_seed and "aia" in style_seed.lower():
        return _PALETTES["aia"]
    return _PALETTES["default"]


def _copy(sb: SceneBrief, *kinds: str) -> str:
    for item in sb.copy_items:
        if item.kind in kinds:
            return item.text
    return ""


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_rich_fallback(
    sb: SceneBrief,
    *,
    block_id: BlockId,
    canvas_w: int,
    canvas_h: int,
    duration: float,
    style_seed: str | None = None,
) -> str:
    """Produce a deterministic, rich-but-safe HTML composition for one scene.

    Always returns output that passes the Hyperframes lint and the
    llm_author validator (no infinite repeats, no rAF, scoped selectors,
    registered timeline).
    """
    pal = _pick_palette(style_seed)
    comp_id = scene_id(sb.index, block_id)
    d = f"{duration:.3f}"

    eyebrow = _escape(_copy(sb, "eyebrow"))
    headline = _escape(_copy(sb, "headline", "stat_value")) or _escape(sb.narrative[:40])
    subhead = _escape(_copy(sb, "subhead", "stat_label"))
    body = _escape(_copy(sb, "body"))
    tagline = _escape(_copy(sb, "tagline"))
    cta = _escape(_copy(sb, "cta"))

    bullets = [_escape(c.text) for c in sb.copy_items if c.kind == "bullet"][:4]
    bullets_html = ""
    if bullets:
        lis = "\n".join(
            f'        <li class="rf-bullet clip" data-start="{0.9 + i*0.15:.3f}" '
            f'data-duration="{max(duration - 0.9 - i*0.15 - 0.4, 0.3):.3f}" '
            f'data-track-index="{20 + i}">'
            f'<span class="rf-bullet-dot"></span>{b}</li>'
            for i, b in enumerate(bullets)
        )
        bullets_html = f'      <ul class="rf-bullets">\n{lis}\n      </ul>'

    # Sub-elements' entry timing plan (track-index strategy: 1 atmos / 2 grain /
    # 3 eyebrow / 4 headline / 5 body+bullets / 6 accent / 7 exit).
    entry_css_vars = (
        f":root{{--bg:{pal['bg']};--ink:{pal['ink']};--accent:{pal['accent']};"
        f"--accent-soft:{pal['accent_soft']};--muted:{pal['muted']};--rule:{pal['rule']}}}"
    )

    # Only render the eyebrow tick + label if we actually have eyebrow copy —
    # otherwise you get an orphaned red mark floating above the headline.
    eyebrow_html = (
        f'<div class="rf-eyebrow-wrap">\n'
        f'        <span class="rf-eyebrow-tick clip" data-start="0.15" data-duration="{max(duration - 0.15 - 0.4, 0.3):.3f}" data-track-index="3"></span>\n'
        f'        <span class="rf-eyebrow clip" data-start="0.2" data-duration="{max(duration - 0.2 - 0.4, 0.3):.3f}" data-track-index="11">{eyebrow}</span>\n'
        f'      </div>'
        if eyebrow else ""
    )
    subhead_html = (
        f'      <div class="rf-subhead clip" data-start="0.55" '
        f'data-duration="{max(duration - 0.55 - 0.4, 0.3):.3f}" data-track-index="12">{subhead}</div>'
        if subhead else ""
    )
    body_html = (
        f'      <div class="rf-body clip" data-start="0.7" '
        f'data-duration="{max(duration - 0.7 - 0.4, 0.3):.3f}" data-track-index="13">{body}</div>'
        if body else ""
    )
    tagline_html = (
        f'      <div class="rf-tagline clip" data-start="0.75" '
        f'data-duration="{max(duration - 0.75 - 0.4, 0.3):.3f}" data-track-index="14">{tagline}</div>'
        if tagline else ""
    )
    cta_html = (
        f'      <div class="rf-cta clip" data-start="0.95" '
        f'data-duration="{max(duration - 0.95 - 0.4, 0.3):.3f}" data-track-index="15">{cta}</div>'
        if cta else ""
    )

    return f"""\
<template id="{comp_id}-template">
  <div data-composition-id="{comp_id}" data-start="0" data-width="{canvas_w}" data-height="{canvas_h}" data-duration="{d}">
    <div class="rf-atmosphere clip" data-start="0" data-duration="{d}" data-track-index="1"></div>
    <div class="rf-orb rf-orb-a clip" data-start="0" data-duration="{d}" data-track-index="8"></div>
    <div class="rf-orb rf-orb-b clip" data-start="0" data-duration="{d}" data-track-index="9"></div>
    <div class="rf-grain clip" data-start="0" data-duration="{d}" data-track-index="2"></div>
    <div class="rf-vignette clip" data-start="0" data-duration="{d}" data-track-index="10"></div>
    <div class="rf-content">
      {eyebrow_html}
      <h1 class="rf-headline clip" data-start="0.35" data-duration="{max(float(d) - 0.35 - 0.4, 0.3):.3f}" data-track-index="4">{headline}</h1>
{subhead_html}
{body_html}
{bullets_html}
{tagline_html}
{cta_html}
    </div>
    <style>
      {entry_css_vars}
      [data-composition-id="{comp_id}"] {{
        position: relative; width: {canvas_w}px; height: {canvas_h}px;
        background: var(--bg); color: var(--ink);
        font-family: "Inter", sans-serif; overflow: hidden;
      }}
      [data-composition-id="{comp_id}"] .rf-atmosphere {{
        position: absolute; inset: 0;
        background: radial-gradient(ellipse at 30% 20%, var(--accent-soft) 0%, transparent 55%),
                    radial-gradient(ellipse at 75% 80%, var(--accent-soft) 0%, transparent 50%),
                    var(--bg);
        transform-origin: 50% 50%;
      }}
      [data-composition-id="{comp_id}"] .rf-orb {{
        position: absolute; width: 780px; height: 780px; border-radius: 50%;
        filter: blur(60px); opacity: 0.28;
      }}
      [data-composition-id="{comp_id}"] .rf-orb-a {{
        background: var(--accent); top: -240px; left: -260px;
      }}
      [data-composition-id="{comp_id}"] .rf-orb-b {{
        background: var(--accent); bottom: -260px; right: -280px; opacity: 0.18;
      }}
      [data-composition-id="{comp_id}"] .rf-grain {{
        position: absolute; inset: -5%;
        background-image: radial-gradient(circle, rgba(0,0,0,0.03) 1px, transparent 1px);
        background-size: 3px 3px; opacity: 0.35; mix-blend-mode: multiply;
      }}
      [data-composition-id="{comp_id}"] .rf-vignette {{
        position: absolute; inset: 0;
        background: radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.18) 100%);
      }}
      [data-composition-id="{comp_id}"] .rf-content {{
        position: absolute; left: 120px; right: 120px; top: 50%;
        transform: translateY(-50%);
        display: flex; flex-direction: column; gap: 28px;
      }}
      [data-composition-id="{comp_id}"] .rf-eyebrow-wrap {{
        display: flex; align-items: center; gap: 14px;
      }}
      [data-composition-id="{comp_id}"] .rf-eyebrow-tick {{
        display: inline-block; width: 40px; height: 3px; background: var(--accent);
      }}
      [data-composition-id="{comp_id}"] .rf-eyebrow {{
        font-size: 22px; font-weight: 700; letter-spacing: 0.18em;
        text-transform: uppercase; color: var(--accent);
      }}
      [data-composition-id="{comp_id}"] .rf-headline {{
        font-size: 112px; font-weight: 800; line-height: 1.02;
        letter-spacing: -0.025em; margin: 0; max-width: 1500px; color: var(--ink);
      }}
      [data-composition-id="{comp_id}"] .rf-subhead {{
        font-size: 34px; font-weight: 600; color: var(--ink); max-width: 1400px; margin: 0;
      }}
      [data-composition-id="{comp_id}"] .rf-body {{
        font-size: 26px; line-height: 1.45; color: var(--muted); max-width: 1200px; margin: 0;
      }}
      [data-composition-id="{comp_id}"] .rf-tagline {{
        font-size: 30px; font-weight: 600; color: var(--accent); letter-spacing: 0.02em; margin: 0;
      }}
      [data-composition-id="{comp_id}"] .rf-cta {{
        font-size: 24px; font-weight: 700; color: var(--ink);
        padding: 14px 28px; border: 2px solid var(--accent); border-radius: 999px;
        align-self: flex-start;
      }}
      [data-composition-id="{comp_id}"] .rf-bullets {{
        list-style: none; margin: 4px 0 0 0; padding: 0;
        display: flex; flex-direction: column; gap: 14px;
      }}
      [data-composition-id="{comp_id}"] .rf-bullet {{
        display: flex; align-items: center; gap: 18px;
        font-size: 26px; color: var(--ink);
      }}
      [data-composition-id="{comp_id}"] .rf-bullet-dot {{
        display: inline-block; width: 12px; height: 12px; border-radius: 50%;
        background: var(--accent); flex-shrink: 0;
      }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <script>
      (function() {{
        window.__timelines = window.__timelines || {{}};
        const compId = "{comp_id}";
        const scope = '[data-composition-id="{comp_id}"]';
        const tl = gsap.timeline({{ paused: true }});

        // Atmospheric — camera push + slow orb drift
        const atmTl = gsap.timeline();
        atmTl.to(scope + ' .rf-atmosphere', {{ scale: 1.05, duration: {d}, ease: 'sine.inOut' }}, 0);
        atmTl.fromTo(scope + ' .rf-orb-a', {{ x: 0, y: 0 }}, {{ x: 40, y: 30, duration: {d}, ease: 'sine.inOut' }}, 0);
        atmTl.fromTo(scope + ' .rf-orb-b', {{ x: 0, y: 0 }}, {{ x: -40, y: -30, duration: {d}, ease: 'sine.inOut' }}, 0);
        atmTl.fromTo(scope + ' .rf-vignette', {{ opacity: 0.3 }}, {{ opacity: 0.6, duration: {d}/2, ease: 'sine.inOut', repeat: 1, yoyo: true }}, 0);

        // Entry choreography
        const introTl = gsap.timeline();
        introTl.from(scope + ' .rf-eyebrow-tick', {{ scaleX: 0, transformOrigin: 'left center', duration: 0.4, ease: 'power3.out' }}, 0);
        introTl.from(scope + ' .rf-eyebrow', {{ x: -20, opacity: 0, duration: 0.5, ease: 'power3.out' }}, 0.05);
        introTl.from(scope + ' .rf-headline', {{ y: 40, opacity: 0, duration: 0.8, ease: 'power3.out' }}, 0.15);
        introTl.from(scope + ' .rf-subhead', {{ y: 20, opacity: 0, duration: 0.55, ease: 'power2.out' }}, 0.4);
        introTl.from(scope + ' .rf-body', {{ y: 15, opacity: 0, duration: 0.55, ease: 'power2.out' }}, 0.5);
        introTl.from(scope + ' .rf-bullet', {{ x: -20, opacity: 0, duration: 0.45, stagger: 0.12, ease: 'power3.out' }}, 0.6);
        introTl.from(scope + ' .rf-tagline', {{ y: 20, opacity: 0, duration: 0.6, ease: 'power3.out' }}, 0.5);
        introTl.from(scope + ' .rf-cta', {{ scale: 0.9, opacity: 0, duration: 0.5, ease: 'back.out(1.6)' }}, 0.7);

        tl.add(atmTl, 0);
        tl.add(introTl, 0.1);
        // Gentle content fade on exit — avoids a cover-panel wipe that would
        // flash the page bg during the cut to the next scene.
        tl.to(scope + ' .rf-content', {{ opacity: 0, y: -20, duration: 0.5, ease: 'power2.in' }}, {max(float(d) - 0.5, 0):.3f});

        window.__timelines[compId] = tl;
      }})();
    </script>
  </div>
</template>
"""
