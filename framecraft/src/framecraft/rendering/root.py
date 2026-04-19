"""Root index.html and meta.json emission. See `.claude/plans/04-assembler.md` §4."""

from __future__ import annotations

import json
from pathlib import Path

from framecraft.rendering.ids import file_name, scene_id
from framecraft.schema import BrandKit, Scene, SceneGraph, TransitionCue


def compute_scene_starts(plan: SceneGraph) -> list[float]:
    """Absolute data-start per scene, accounting for transition overlaps.

    The transition from scene i into i+1 reduces the effective advance by its
    overlap value, so scene i+1 starts at (cumulative - overlap).
    """
    starts: list[float] = []
    t = 0.0
    cue_by_from = {c.from_scene: c for c in plan.transitions}
    for i, scene in enumerate(plan.scenes):
        starts.append(round(t, 3))
        t += scene.duration
        cue = cue_by_from.get(i)
        if cue is not None:
            t -= cue.overlap
    return starts


def transition_starts(plan: SceneGraph, scene_starts: list[float]) -> list[float]:
    """Per-transition absolute start = start of its to_scene - overlap."""
    return [round(scene_starts[c.to_scene] - c.overlap, 3) for c in plan.transitions]


def render_index_html(plan: SceneGraph, *, project_name: str) -> str:
    w, h = plan.canvas
    total = plan.duration
    brand = plan.brand_kit
    bg = _brand_bg(brand)
    font = _brand_font(brand)
    font_link = _font_link_tag(font)
    starts = compute_scene_starts(plan)
    t_starts = transition_starts(plan, starts)

    placeholders: list[str] = []
    for scene, start in zip(plan.scenes, starts, strict=True):
        placeholders.append(_scene_placeholder(scene, start=start, canvas=(w, h)))
    for cue, start in zip(plan.transitions, t_starts, strict=True):
        placeholders.append(_transition_placeholder(cue, start=start, canvas=(w, h)))

    audio_bed = ""
    music = plan.brief.music_path
    if music is not None:
        ext = music.suffix.lstrip(".").lower() or "mp3"
        vol = plan.brief.music_volume
        audio_bed = (
            f'      <audio id="music-bed" src="assets/music.{ext}" '
            f'data-start="0" data-duration="{total:.3f}" '
            f'data-track-index="20" data-volume="{vol:g}"></audio>\n'
        )

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="UTF-8" />\n'
        f'    <meta name="viewport" content="width={w}, height={h}" />\n'
        f"    <title>{_escape(project_name)}</title>\n"
        '    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>\n'
        f"{font_link}"
        "    <style>\n"
        "      * { margin: 0; padding: 0; box-sizing: border-box; }\n"
        f"      html, body {{ width: {w}px; height: {h}px; overflow: hidden; "
        f'background: {bg}; font-family: "{_escape(font)}", sans-serif; }}\n'
        "      .scene { position: absolute; inset: 0; }\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        '    <div id="root" data-composition-id="main"\n'
        f'         data-start="0" data-duration="{total:.3f}"\n'
        f'         data-width="{w}" data-height="{h}">\n'
        f"{''.join(placeholders)}"
        f"{audio_bed}"
        "    </div>\n"
        "    <script>\n"
        "      window.__timelines = window.__timelines || {};\n"
        "      const tl = gsap.timeline({ paused: true });\n"
        '      window.__timelines["main"] = tl;\n'
        "    </script>\n"
        "  </body>\n"
        "</html>\n"
    )


def render_meta_json(plan: SceneGraph, *, project_name: str, project_id: str) -> str:
    payload = {
        "id": project_id,
        "name": project_name,
        "aspect": plan.aspect.value,
        "duration": plan.duration,
        "fps": plan.brief.fps,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_root(out_dir: Path, plan: SceneGraph, *, project_name: str, project_id: str) -> None:
    (out_dir / "index.html").write_text(
        render_index_html(plan, project_name=project_name), encoding="utf-8"
    )
    (out_dir / "meta.json").write_text(
        render_meta_json(plan, project_name=project_name, project_id=project_id),
        encoding="utf-8",
    )


# --- helpers ---------------------------------------------------------------


def _scene_placeholder(
    scene: Scene,
    *,
    start: float,
    canvas: tuple[int, int],
) -> str:
    w, h = canvas
    sid = scene_id(scene.index, scene.block_id)
    src = f"compositions/{file_name(scene.index, scene.block_id)}"
    return (
        f'      <div id="{sid}" class="scene"\n'
        f'           data-composition-id="{sid}"\n'
        f'           data-composition-src="{src}"\n'
        f'           data-start="{start:.3f}" data-duration="{scene.duration:.3f}" '
        f'data-track-index="{scene.track_index}"\n'
        f'           data-width="{w}" data-height="{h}"></div>\n'
    )


def _transition_placeholder(
    cue: TransitionCue,
    *,
    start: float,
    canvas: tuple[int, int],
) -> str:
    w, h = canvas
    tid = f"t-{cue.from_scene:02d}-{cue.block_id.value}"
    src = f"compositions/transitions/{tid}.html"
    return (
        f'      <div id="{tid}" class="scene"\n'
        f'           data-composition-id="{tid}"\n'
        f'           data-composition-src="{src}"\n'
        f'           data-start="{start:.3f}" data-duration="{cue.overlap:.3f}" '
        f'data-track-index="10"\n'
        f'           data-width="{w}" data-height="{h}"></div>\n'
    )


def _brand_bg(brand: BrandKit | None) -> str:
    if brand and brand.palette:
        return brand.palette.bg
    return "#09090C"


def _brand_font(brand: BrandKit | None) -> str:
    if brand and brand.typography:
        return brand.typography.headline
    return "Inter"


def _font_link_tag(font: str) -> str:
    safe = font.replace(" ", "+")
    return (
        f'    <link rel="stylesheet" '
        f'href="https://fonts.googleapis.com/css2?family={safe}:wght@300;400;500;600;700;800;900'
        f'&display=block" />\n'
    )


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
