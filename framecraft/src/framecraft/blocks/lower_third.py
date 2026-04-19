"""lower-third — name plate sliding in from the left.

Intended as an overlay (higher data-track-index) above scene content, but in
M1 we also use it as a standalone scene for speaker introductions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from framecraft.blocks._spec import BlockSpec
from framecraft.rendering.ids import scene_id
from framecraft.rendering.native import scene_template
from framecraft.schema import Aspect, BlockId, Category, Provenance


class LowerThirdProps(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: str | None = Field(default=None, max_length=80)
    accent: str = "#C44536"  # left-rule color
    fg: str = "#FFFFFF"
    bg: str = "#09090C"
    font: str = "Inter"


def _render(
    props: dict[str, Any],
    scene_index: int,
    canvas_w: int,
    canvas_h: int,
    duration: float,
) -> str:
    p = LowerThirdProps.model_validate(props)
    comp_id = scene_id(scene_index, BlockId.LOWER_THIRD)

    role_html = (
        f'<p class="clip role" data-start="0.25" data-duration="{max(duration - 0.25, 0.5):.3f}" '
        f'data-track-index="1">{_escape(p.role)}</p>'
        if p.role
        else ""
    )

    body_html = (
        f'<div class="plate">\n'
        f'      <div class="rule clip" data-start="0" data-duration="{duration:.3f}" '
        f'data-track-index="1"></div>\n'
        f'      <div class="text">\n'
        f'        <h2 class="clip name" data-start="0.1" data-duration="{max(duration - 0.1, 0.5):.3f}" '
        f'data-track-index="1">{_escape(p.name)}</h2>\n'
        f"        {role_html}\n"
        f"      </div>\n"
        f"    </div>"
    )

    # Lower-thirds sit in the lower-left quadrant regardless of aspect.
    plate_bottom = int(canvas_h * 0.14)
    plate_left = int(canvas_w * 0.06)

    style_css = (
        f'[data-composition-id="{comp_id}"] {{ '
        f"width: {canvas_w}px; height: {canvas_h}px; "
        f"background: {p.bg}; color: {p.fg}; "
        f'font-family: "{_escape(p.font)}", sans-serif; '
        "position: relative; overflow: hidden; }"
        f'[data-composition-id="{comp_id}"] .plate {{ '
        f"position: absolute; bottom: {plate_bottom}px; left: {plate_left}px; "
        "display: flex; align-items: stretch; gap: 20px; }"
        f'[data-composition-id="{comp_id}"] .rule {{ '
        f"width: 4px; background: {p.accent}; transform: scaleY(0); "
        "transform-origin: bottom; }"
        f'[data-composition-id="{comp_id}"] .text {{ '
        "display: flex; flex-direction: column; justify-content: center; }"
        f'[data-composition-id="{comp_id}"] .name {{ '
        "font-size: 56px; font-weight: 700; letter-spacing: -0.01em; "
        "margin: 0; opacity: 0; transform: translateX(-24px); }"
        f'[data-composition-id="{comp_id}"] .role {{ '
        "font-size: 24px; font-weight: 400; opacity: 0; margin: 4px 0 0 0; "
        "letter-spacing: 0.02em; }"
    )

    timeline_js = (
        "const tl = gsap.timeline({ paused: true });\n"
        f'      tl.to("[data-composition-id=\\"{comp_id}\\"] .rule", '
        '{ scaleY: 1, duration: 0.5, ease: "power3.out" }, 0);\n'
        f'      tl.to("[data-composition-id=\\"{comp_id}\\"] .name", '
        '{ opacity: 1, x: 0, duration: 0.6, ease: "power2.out" }, 0.1);\n'
    )
    if p.role:
        timeline_js += (
            f'      tl.to("[data-composition-id=\\"{comp_id}\\"] .role", '
            '{ opacity: 1, duration: 0.5, ease: "power2.out" }, 0.25);\n'
        )

    return scene_template(
        composition_id=comp_id,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        duration=duration,
        body_html=body_html,
        style_css=style_css,
        timeline_js=timeline_js,
    )


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


SPEC = BlockSpec(
    id=BlockId.LOWER_THIRD,
    category=Category.TITLE,
    provenance=Provenance.NATIVE,
    synopsis="Name + role plate with a colored rule that slides in from the left.",
    suggested_duration=(2.0, 5.0),
    aspect_preferred=[Aspect.AR_16_9, Aspect.AR_9_16, Aspect.AR_1_1],
    required_props=LowerThirdProps,
    template=_render,
)
