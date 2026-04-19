"""FrameCraft — situation → Hyperframes project."""

from framecraft.blocks._spec import BlockSpec, SlotSpec
from framecraft.director import Director, DirectorError
from framecraft.schema import (
    Archetype,
    Aspect,
    BlockId,
    Brief,
    BrandKit,
    Caption,
    Category,
    Mood,
    Palette,
    Provenance,
    Scene,
    SceneGraph,
    TransitionCue,
    TransitionId,
    Typography,
)

__version__ = "0.0.1"

__all__ = [
    "Archetype",
    "Aspect",
    "BlockId",
    "BlockSpec",
    "Brief",
    "BrandKit",
    "Caption",
    "Category",
    "Director",
    "DirectorError",
    "Mood",
    "Palette",
    "Provenance",
    "Scene",
    "SceneGraph",
    "SlotSpec",
    "TransitionCue",
    "TransitionId",
    "Typography",
]
