"""end-card — closing tagline with subtle fade-up."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from framecraft.blocks._spec import BlockSpec
from framecraft.rendering.ids import scene_id
from framecraft.rendering.native import scene_template
from framecraft.schema import Aspect, BlockId, Category, Provenance


class EndCardProps(BaseModel):
    tagline: str = Field(min_length=1, max_length=80)
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
    p = EndCardProps.model_validate(props)
    comp_id = scene_id(scene_index, BlockId.END_CARD)

    body_html = (
        f'<p class="clip tagline" data-start="0" data-duration="{duration:.3f}" '
        f'data-track-index="1">{_escape(p.tagline)}</p>'
    )

    style_css = (
        f'[data-composition-id="{comp_id}"] {{ '
        f"width: {canvas_w}px; height: {canvas_h}px; "
        f"background: {p.bg}; color: {p.fg}; "
        f'font-family: "{_escape(p.font)}", sans-serif; '
        "display: flex; align-items: center; justify-content: center; "
        "position: relative; overflow: hidden; }"
        f'[data-composition-id="{comp_id}"] .tagline {{ '
        "font-size: 56px; font-weight: 500; letter-spacing: -0.01em; "
        "opacity: 0; transform: translateY(16px); margin: 0; }"
    )

    timeline_js = (
        "const tl = gsap.timeline({ paused: true });\n"
        f'      tl.to("[data-composition-id=\\"{comp_id}\\"] .tagline", '
        '{ opacity: 1, y: 0, duration: 0.9, ease: "power2.out" }, 0);\n'
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
    id=BlockId.END_CARD,
    category=Category.TITLE,
    provenance=Provenance.NATIVE,
    synopsis="Closing tagline that fades up. Pairs with title-card.",
    suggested_duration=(1.5, 4.0),
    aspect_preferred=[Aspect.AR_16_9, Aspect.AR_9_16, Aspect.AR_1_1],
    required_props=EndCardProps,
    template=_render,
)
