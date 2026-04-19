"""Jinja environment and helpers for NATIVE blocks.

See `.claude/plans/04-assembler.md` §6–7.
"""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined

_env: Environment | None = None


def env() -> Environment:
    """Return the shared Jinja env (built lazily, cached)."""
    global _env
    if _env is None:
        _env = Environment(
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )
    return _env


def scene_template(
    *,
    composition_id: str,
    canvas_w: int,
    canvas_h: int,
    duration: float,
    body_html: str,
    style_css: str,
    timeline_js: str,
) -> str:
    """Wrap a block's inner markup in the Hyperframes §6.9 contract.

    The inner body may carry class="clip" on timed elements. This wrapper:
    - Opens a <template id="<id>-template">
    - Emits the root <div data-composition-id=... data-width data-height data-duration>
    - Inlines <style>, the body, and the <script> that registers the timeline on
      window.__timelines["<composition-id>"]
    - Closes the </template>.
    """
    return (
        f'<template id="{composition_id}-template">\n'
        f'  <div data-composition-id="{composition_id}" '
        f'data-start="0" '
        f'data-width="{canvas_w}" data-height="{canvas_h}" '
        f'data-duration="{duration:.3f}">\n'
        f"    <style>{style_css}</style>\n"
        f"    {body_html}\n"
        '    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>\n'
        f"    <script>\n"
        f"      window.__timelines = window.__timelines || {{}};\n"
        f"      {timeline_js}\n"
        f'      window.__timelines["{composition_id}"] = tl;\n'
        f"    </script>\n"
        f"  </div>\n"
        f"</template>\n"
    )
