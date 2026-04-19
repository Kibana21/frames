"""title-card — full-screen headline with fade-in, optional subtitle.

NATIVE block. Honors BrandKit palette and typography if provided.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from framecraft.blocks._spec import BlockSpec
from framecraft.rendering.ids import scene_id
from framecraft.rendering.native import scene_template
from framecraft.schema import Aspect, BlockId, Category, Provenance


class TitleCardProps(BaseModel):
    headline: str = Field(min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=180)
    bg: str = "#09090C"
    fg: str = "#FFFFFF"
    font: str = "Inter"


def _render(
    props: dict[str, Any],
    scene_index: int,
    canvas_w: int,
    canvas_h: int,
    duration: float,
) -> str:
    p = TitleCardProps.model_validate(props)
    comp_id = scene_id(scene_index, BlockId.TITLE_CARD)

    sub_html = (
        f'<p class="clip subtitle" data-start="0.4" data-duration="{max(duration - 0.4, 0.5):.3f}" '
        f'data-track-index="1">{_escape(p.subtitle)}</p>'
        if p.subtitle
        else ""
    )

    body_html = (
        f'<h1 class="clip headline" data-start="0" data-duration="{duration:.3f}" '
        f'data-track-index="1">{_escape(p.headline)}</h1>\n'
        f"    {sub_html}"
    )

    style_css = (
        f'[data-composition-id="{comp_id}"] {{ '
        f"width: {canvas_w}px; height: {canvas_h}px; "
        f"background: {p.bg}; color: {p.fg}; "
        f'font-family: "{_escape(p.font)}", sans-serif; '
        "display: flex; flex-direction: column; align-items: center; "
        "justify-content: center; position: relative; overflow: hidden; }"
        f'[data-composition-id="{comp_id}"] .headline {{ '
        "font-size: 96px; font-weight: 800; text-align: center; "
        "letter-spacing: -0.02em; line-height: 1.05; margin: 0; opacity: 0; }"
        f'[data-composition-id="{comp_id}"] .subtitle {{ '
        "font-size: 28px; font-weight: 400; text-align: center; opacity: 0; "
        "margin-top: 24px; }"
    )

    timeline_js = (
        "const tl = gsap.timeline({ paused: true });\n"
        f'      tl.to("[data-composition-id=\\"{comp_id}\\"] .headline", '
        '{ opacity: 1, duration: 0.8, ease: "power2.out" }, 0);\n'
    )
    if p.subtitle:
        timeline_js += (
            f'      tl.to("[data-composition-id=\\"{comp_id}\\"] .subtitle", '
            '{ opacity: 1, duration: 0.6, ease: "power2.out" }, 0.4);\n'
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
    id=BlockId.TITLE_CARD,
    category=Category.TITLE,
    provenance=Provenance.NATIVE,
    synopsis="Full-screen headline with fade-in; optional subtitle below.",
    suggested_duration=(2.0, 6.0),
    aspect_preferred=[Aspect.AR_16_9, Aspect.AR_9_16, Aspect.AR_1_1],
    required_props=TitleCardProps,
    template=_render,
)
