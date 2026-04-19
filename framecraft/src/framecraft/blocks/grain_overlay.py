"""grain-overlay — SVG-turbulence film grain sits on top of everything.

Tiny file, pure CSS+SVG. Use on a HIGH data-track-index so it layers above
other scenes. Not usually a standalone scene — but it conforms to the same
contract so the registry stays uniform.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from framecraft.blocks._spec import BlockSpec
from framecraft.rendering.ids import scene_id
from framecraft.rendering.native import scene_template
from framecraft.schema import Aspect, BlockId, Category, Provenance


class GrainOverlayProps(BaseModel):
    opacity: float = Field(default=0.08, ge=0.0, le=0.4)
    base_frequency: float = Field(default=0.9, ge=0.1, le=3.0)
    seed: int = Field(default=1, ge=0, le=99)


def _render(
    props: dict[str, Any],
    scene_index: int,
    canvas_w: int,
    canvas_h: int,
    duration: float,
) -> str:
    p = GrainOverlayProps.model_validate(props)
    comp_id = scene_id(scene_index, BlockId.GRAIN_OVERLAY)

    # Inline SVG turbulence as a background image via data URI.
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{canvas_w}' height='{canvas_h}'>"
        f"<filter id='n'>"
        f"<feTurbulence type='fractalNoise' baseFrequency='{p.base_frequency}' "
        f"numOctaves='2' seed='{p.seed}' />"
        f"</filter>"
        f"<rect width='100%' height='100%' filter='url(%23n)' opacity='{p.opacity}' />"
        f"</svg>"
    )
    data_uri = "data:image/svg+xml;utf8," + svg.replace("#", "%23").replace("<", "%3C").replace(">", "%3E")

    body_html = (
        f'<div class="grain clip" data-start="0" data-duration="{duration:.3f}" '
        f'data-track-index="1"></div>'
    )

    style_css = (
        f'[data-composition-id="{comp_id}"] {{ '
        f"width: {canvas_w}px; height: {canvas_h}px; "
        "position: relative; overflow: hidden; pointer-events: none; }"
        f'[data-composition-id="{comp_id}"] .grain {{ '
        "position: absolute; inset: 0; "
        f"background-image: url(\"{data_uri}\"); "
        "mix-blend-mode: overlay; }"
    )

    timeline_js = "const tl = gsap.timeline({ paused: true });\n"

    return scene_template(
        composition_id=comp_id,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        duration=duration,
        body_html=body_html,
        style_css=style_css,
        timeline_js=timeline_js,
    )


SPEC = BlockSpec(
    id=BlockId.GRAIN_OVERLAY,
    category=Category.BACKGROUND,
    provenance=Provenance.NATIVE,
    synopsis="Film-grain noise overlay on a high track-index. Tones down clinical UI.",
    suggested_duration=(1.0, 30.0),
    aspect_preferred=[Aspect.AR_16_9, Aspect.AR_9_16, Aspect.AR_1_1],
    required_props=GrainOverlayProps,
    template=_render,
)
