"""BlockSpec type — lives here (not in schema.py) to avoid circular imports
between schema and the blocks that register against it.
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from framecraft.schema import Aspect, BlockId, Category, Provenance


class SlotSpec(BaseModel):
    kind: Literal["text", "css_var", "attr", "asset_path"]
    selector: str
    target: str
    llm_polish: bool = False
    max_length: int | None = None


class BlockSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: BlockId
    category: Category
    synopsis: str = Field(max_length=140)
    provenance: Provenance
    suggested_duration: tuple[float, float]
    aspect_preferred: list[Aspect]
    fallback_block_id: BlockId | None = None

    # Prop schemas — the Pydantic model every scene.block_props is validated
    # against. `required_props` is a Pydantic model type, not an instance.
    # Optional props are folded into the same model via Field(default=...).
    required_props: type[BaseModel] | None = None
    optional_props: type[BaseModel] | None = None

    # NATIVE-only
    template: Callable[[dict[str, Any], int, int, int, float], str] | None = None

    # CATALOG-only
    catalog_id: str | None = None
    catalog_version: str | None = None
    catalog_hash: str | None = None
    install_command: str | None = None
    slots: dict[str, SlotSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _provenance_coherent(self) -> "BlockSpec":
        if self.provenance is Provenance.NATIVE:
            if self.template is None:
                raise ValueError(f"{self.id}: NATIVE provenance requires a template")
            if any([self.catalog_id, self.catalog_version, self.catalog_hash]):
                raise ValueError(f"{self.id}: NATIVE must not set catalog_*")
            if self.slots:
                raise ValueError(f"{self.id}: NATIVE must not declare slots")
        else:  # CATALOG
            if self.template is not None:
                raise ValueError(f"{self.id}: CATALOG must not set a template")
            if not all([self.catalog_id, self.catalog_version, self.catalog_hash]):
                raise ValueError(f"{self.id}: CATALOG must set catalog_id/version/hash")
            if not self.slots:
                raise ValueError(f"{self.id}: CATALOG must declare at least one slot")
        return self
