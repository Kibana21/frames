"""Rendering helpers — splits NATIVE/CATALOG paths, root index.html, ID utils."""

from framecraft.rendering.ids import fc_id, file_name, scene_id
from framecraft.rendering.native import env, scene_template

__all__ = ["env", "fc_id", "file_name", "scene_id", "scene_template"]

# Submodules available but not star-imported to avoid heavy bs4 load at package level:
# framecraft.rendering.catalog  — CATALOG install + slot injection
# framecraft.rendering.html_walker — deterministic DOM manipulation
# framecraft.rendering.audio    — audio bed file copy
