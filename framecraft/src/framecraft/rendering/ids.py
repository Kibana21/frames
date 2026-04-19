"""Deterministic ID helpers — derived from indices and block ids, never timestamps.

See `.claude/plans/04-assembler.md` §3 and 00-plan-index §5 (determinism contract).
"""

from __future__ import annotations

import re

from framecraft.schema import BlockId

_SAFE = re.compile(r"[^a-z0-9]+")


def scene_id(scene_index: int, block_id: BlockId) -> str:
    """scene_id(2, BlockId.TITLE_CARD) -> 'scene-02-title-card'."""
    return f"scene-{scene_index:02d}-{block_id.value}"


def fc_id(scene_index: int, local: str) -> str:
    """Deterministic element ID within a scene.

    fc_id(2, 'chart-bar-3') -> 'scene-02-chart-bar-3'.
    """
    slug = _SAFE.sub("-", local.lower()).strip("-")
    return f"scene-{scene_index:02d}-{slug}"


def file_name(scene_index: int, block_id: BlockId) -> str:
    """Per-scene HTML file name."""
    return f"{scene_id(scene_index, block_id)}.html"
