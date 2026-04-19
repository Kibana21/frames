"""Block registry. Populated at import time by `framecraft.blocks`.

See `.claude/plans/01-schema-and-registry.md` §8–9.
"""

from __future__ import annotations

from framecraft.blocks._spec import BlockSpec
from framecraft.schema import Archetype, BlockId, Category, TransitionId

# Policy table — which categories each archetype may select from.
ARCHETYPE_BLOCK_POLICY: dict[Archetype, set[Category]] = {
    Archetype.NARRATIVE_SCENE: {Category.TITLE, Category.BACKGROUND, Category.TRANSITION},
    Archetype.PRODUCT_PROMO: {
        Category.TITLE, Category.BACKGROUND, Category.BRANDING,
        Category.PRODUCT, Category.TRANSITION,
    },
    Archetype.DATA_EXPLAINER: {
        Category.TITLE, Category.BACKGROUND, Category.DATA, Category.TRANSITION,
    },
    Archetype.UI_WALKTHROUGH: {
        Category.TITLE, Category.BACKGROUND, Category.PRODUCT,
        Category.NOTIFICATION, Category.TRANSITION,
    },
    Archetype.SOCIAL_CARD: {
        Category.TITLE, Category.BACKGROUND, Category.SOCIAL,
    },
}


class BlockRegistry:
    """Read-only facade over the block and transition maps."""

    def __init__(
        self,
        blocks: dict[BlockId, BlockSpec],
        transitions: dict[TransitionId, BlockSpec] | None = None,
    ) -> None:
        self._blocks = blocks
        self._transitions = transitions or {}

    def resolve(self, block_id: BlockId) -> BlockSpec:
        try:
            return self._blocks[block_id]
        except KeyError as e:
            raise KeyError(f"Unknown block id: {block_id!r}") from e

    def allowed_for(self, archetype: Archetype) -> list[BlockId]:
        categories = ARCHETYPE_BLOCK_POLICY[archetype]
        return sorted(
            (bid for bid, spec in self._blocks.items() if spec.category in categories),
            key=lambda b: b.value,
        )

    def transitions_allowed(self) -> list[TransitionId]:
        return sorted(self._transitions.keys(), key=lambda t: t.value)

    def all(self) -> dict[BlockId, BlockSpec]:
        return dict(self._blocks)


REGISTRY: dict[BlockId, BlockSpec] = {}
TRANSITIONS: dict[TransitionId, BlockSpec] = {}


def _register(spec: BlockSpec) -> None:
    if spec.category is Category.TRANSITION:
        if spec.id in TRANSITIONS:
            raise RuntimeError(f"Duplicate transition id: {spec.id}")
        TRANSITIONS[spec.id] = spec  # type: ignore[index]  # id is BlockId but cast via shared enum values
        return
    if spec.id in REGISTRY:
        raise RuntimeError(f"Duplicate block id at import time: {spec.id}")
    REGISTRY[spec.id] = spec


def _bootstrap() -> None:
    """Walk `framecraft.blocks`, import every submodule, register its SPEC.

    Modules whose name starts with `_` are skipped (treated as helpers).
    Duplicate IDs raise at import time — this is intentional; the gen script
    also prevents duplicates by convention but runtime safety is free.
    """
    import importlib
    import pkgutil

    import framecraft.blocks as blocks_pkg

    for info in pkgutil.iter_modules(blocks_pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"framecraft.blocks.{info.name}")
        spec = getattr(module, "SPEC", None)
        if spec is None:
            continue
        _register(spec)


_bootstrap()


def default_registry() -> BlockRegistry:
    return BlockRegistry(REGISTRY, TRANSITIONS)
