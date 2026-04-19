"""gradient-bg — a slowly drifting radial-or-linear gradient backdrop.

Designed to sit beneath text blocks on a lower track-index. Provides subtle
motion without competing for attention.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from framecraft.blocks._spec import BlockSpec
from framecraft.rendering.ids import scene_id
from framecraft.rendering.native import scene_template
from framecraft.schema import Aspect, BlockId, Category, Provenance


class GradientBgProps(BaseModel):
    color_a: str = "#09090C"
    color_b: str = "#1C1C22"
    color_c: str | None = "#2A2438"
    mode: Literal["radial", "linear"] = "radial"
    angle_deg: float = Field(default=135.0, ge=0, le=360)
    drift: bool = True  # subtle position animation


def _render(
    props: dict[str, Any],
    scene_index: int,
    canvas_w: int,
    canvas_h: int,
    duration: float,
) -> str:
    p = GradientBgProps.model_validate(props)
    comp_id = scene_id(scene_index, BlockId.GRADIENT_BG)

    stops = [p.color_a, p.color_b]
    if p.color_c:
        stops.append(p.color_c)
    stops_css = ", ".join(stops)

    if p.mode == "radial":
        base_bg = f"radial-gradient(circle at 30% 40%, {stops_css})"
    else:
        base_bg = f"linear-gradient({p.angle_deg}deg, {stops_css})"

    body_html = (
        f'<div class="bg clip" data-start="0" data-duration="{duration:.3f}" '
        f'data-track-index="1"></div>'
    )

    style_css = (
        f'[data-composition-id="{comp_id}"] {{ '
        f"width: {canvas_w}px; height: {canvas_h}px; "
        "position: relative; overflow: hidden; }"
        f'[data-composition-id="{comp_id}"] .bg {{ '
        "position: absolute; inset: -10%; "
        f"background: {base_bg}; "
        "background-size: 130% 130%; background-position: 0% 0%; "
        "filter: blur(0.5px); }"
    )

    if p.drift:
        timeline_js = (
            "const tl = gsap.timeline({ paused: true });\n"
            f'      tl.to("[data-composition-id=\\"{comp_id}\\"] .bg", '
            "{ backgroundPosition: \"100% 100%\", "
            f'duration: {duration:.3f}, ease: "none" }}, 0);\n'
        )
    else:
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
    id=BlockId.GRADIENT_BG,
    category=Category.BACKGROUND,
    provenance=Provenance.NATIVE,
    synopsis="Slowly drifting gradient (radial or linear). Sits beneath content.",
    suggested_duration=(2.0, 30.0),
    aspect_preferred=[Aspect.AR_16_9, Aspect.AR_9_16, Aspect.AR_1_1],
    required_props=GradientBgProps,
    template=_render,
)
