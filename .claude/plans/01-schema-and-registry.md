# 01 — Schema & Block Registry

## Goal

Define the `SceneGraph` Pydantic v2 type system and a discoverable block registry. Every downstream subsystem consumes these types; they are the single source of truth for what a plan is and what blocks exist.

## Inputs

- PRD §3 US-002, US-003; §6.2, §6.2.5, §6.8; §10 OQ-2.
- Hyperframes HTML schema (`data-composition-id`, `data-start`, `data-duration`, `data-width`, `data-height`, `class="clip"`) — reference for validator rules.

## Outputs

- Importable types: `Brief`, `SceneGraph`, `Scene`, `TransitionCue`, `Caption`, `BrandKit`, `Palette`, `Typography`, `BlockSpec`, `SlotSpec`, `ProviderResponse` (forward ref, defined in 02).
- Enums: `Aspect`, `Mood`, `Archetype`, `BlockId`, `TransitionId`, `Provenance`, `Category`.
- `REGISTRY: dict[BlockId, BlockSpec]` populated by module-import-time discovery of `framecraft/blocks/*.py`.
- `BlockRegistry.allowed_for(archetype: Archetype) -> list[BlockId]`.
- `framecraft/music.py` validator function.

## Critical files

| Path | Purpose |
| --- | --- |
| `framecraft/schema.py` | All Pydantic models and enums |
| `framecraft/registry.py` | Registry discovery, `allowed_for`, `resolve` |
| `framecraft/blocks/__init__.py` | Re-exports each block module; triggers registration |
| `framecraft/blocks/<block_id>.py` | One file per block; exports `SPEC: BlockSpec` |
| `framecraft/music.py` | Audio-bed validator (US-016 split) |
| `tests/test_schema.py` | Unit tests: 6 invalid graphs, each asserts a distinct validator fires |
| `tests/test_registry.py` | Unit tests: discovery, `allowed_for`, bad-spec rejection |

## Dependencies

None. This plan is the root of the dependency tree.

## Implementation steps

1. **Enums.**
   - `Aspect(StrEnum)`: `AR_16_9="16:9"`, `AR_9_16="9:16"`, `AR_1_1="1:1"`. Expose a `.dimensions: tuple[int, int]` cached property returning `(1920,1080)`, `(1080,1920)`, `(1080,1080)` respectively.
   - `Mood(StrEnum)`: `CINEMATIC`, `PLAYFUL`, `SERIOUS`, `TECHNICAL`, `WARM`.
   - `Archetype(StrEnum)`: `NARRATIVE_SCENE`, `PRODUCT_PROMO`, `DATA_EXPLAINER`, `UI_WALKTHROUGH`, `SOCIAL_CARD`.
   - `Provenance(StrEnum)`: `NATIVE`, `CATALOG`.
   - `Category(StrEnum)`: `TITLE`, `BACKGROUND`, `BRANDING`, `PRODUCT`, `DATA`, `SOCIAL`, `NOTIFICATION`, `TRANSITION`.
   - `BlockId(StrEnum)` and `TransitionId(StrEnum)` are populated from registry file scan during `registry.py` import — but the enum is declared statically for type checkers. Use a code-generated `_block_ids.py` committed to the repo; regenerate via `scripts/gen_block_ids.py`.

2. **`Palette`, `Typography`, `BrandKit` models.**
   - `Palette(primary: str, bg: str, accent: str)` — regex-validated `#RRGGBB`.
   - `Typography(headline: str = "Inter", body: str = "Inter", weight_range: tuple[int, int] = (300, 900))` — only Google Fonts names allowed (soft validator: warn if unknown).
   - `BrandKit(logo_path: Path | None = None, palette: Palette | None = None, typography: Typography | None = None)`.

3. **`SlotSpec`.**
   ```python
   class SlotSpec(BaseModel):
       kind: Literal["text", "css_var", "attr", "asset_path"]
       selector: str
       target: str
       llm_polish: bool = False
       max_length: int | None = None  # for text kinds, informs linter
   ```

4. **`BlockSpec`.**
   ```python
   class BlockSpec(BaseModel):
       id: BlockId
       category: Category
       synopsis: str = Field(max_length=140)  # LLM-facing
       provenance: Provenance
       suggested_duration: tuple[float, float]
       aspect_preferred: list[Aspect]
       fallback_block_id: BlockId | None = None  # OQ-2 landing spot
       required_props: type[BaseModel]
       optional_props: type[BaseModel] | None = None
       # NATIVE-only
       template: Callable[[BaseModel], str] | None = None
       # CATALOG-only
       catalog_id: str | None = None
       catalog_version: str | None = None
       catalog_hash: str | None = None  # SHA-256 of installed tree
       install_command: str | None = None
       slots: dict[str, SlotSpec] = {}
   ```
   Model validator: `provenance == NATIVE` ⇒ `template is not None`, all catalog_* None, slots empty. `provenance == CATALOG` ⇒ all four catalog fields set, `template is None`, slots non-empty. Mismatch raises `ValueError` at import time.

5. **`Scene`, `TransitionCue`, `Caption`.**
   ```python
   class Scene(BaseModel):
       index: int = Field(ge=0)
       block_id: BlockId
       start: float = Field(ge=0)
       duration: float = Field(gt=0)
       track_index: int = Field(ge=1, default=1)
       block_props: dict[str, Any]  # validated against spec at SceneGraph level
       polished: dict[str, str] = {}  # §6.4 polish cache

   class TransitionCue(BaseModel):
       from_scene: int = Field(ge=0)
       to_scene: int = Field(ge=1)
       block_id: TransitionId
       overlap: float = Field(ge=0.3, le=1.5)

       @model_validator(mode="after")
       def _check_adjacency(self):
           if self.to_scene != self.from_scene + 1:
               raise ValueError("transitions connect adjacent scenes only")
           return self

   class Caption(BaseModel):  # v1 unused, reserved
       start: float
       duration: float
       text: str
   ```

6. **`Brief` and `SceneGraph`.**
   ```python
   class Brief(BaseModel):
       situation: str = Field(min_length=3, max_length=2000)
       aspect: Aspect = Aspect.AR_16_9
       duration: float = Field(ge=3, le=300, default=20)
       fps: int = Field(default=30)
       mood: Mood | None = None
       archetype: Archetype | None = None  # when user forced it
       brand_kit: BrandKit | None = None
       music_path: Path | None = None

   class SceneGraph(BaseModel):
       version: Literal[1] = 1
       brief: Brief
       archetype: Archetype
       aspect: Aspect
       canvas: tuple[int, int]   # derived, validated == aspect.dimensions
       duration: float
       scenes: list[Scene] = Field(min_length=1, max_length=12)
       transitions: list[TransitionCue] = []
       brand_kit: BrandKit | None = None
       # Assembler side-cars, persisted to plan.json:
       # none at SceneGraph level; polished lives per-Scene
   ```

7. **Cross-field validators on `SceneGraph`.**
   - `canvas == aspect.dimensions` — strict.
   - Every `scene.block_id` ∈ `REGISTRY`.
   - `scene.start` values non-decreasing, no duplicates.
   - `sum(scene.duration) - sum(transition.overlap) ≈ duration ± 0.1s`.
   - `scene.block_props` validates against `REGISTRY[scene.block_id].required_props` (and optional_props).
   - Transition indices reference existing scenes.
   - `scene.index` values are a 0-based contiguous sequence.

8. **Registry discovery.**
   - `framecraft/blocks/__init__.py` walks the package and imports every submodule.
   - Each submodule exports `SPEC: BlockSpec`; on import, `registry.py` binds `SPEC.id → SPEC` into the singleton `REGISTRY`.
   - Duplicate `id` → fatal on import.
   - Transitions live under `framecraft/blocks/transitions/` and register into a parallel `TRANSITIONS: dict[TransitionId, BlockSpec]`.
   - Missing `_block_ids.py` → regenerate via `scripts/gen_block_ids.py` that scans `framecraft/blocks/` and writes the two enums.

9. **`BlockRegistry` facade.**
   ```python
   class BlockRegistry:
       def __init__(self, blocks: dict[BlockId, BlockSpec], transitions: dict[TransitionId, BlockSpec]): ...
       def allowed_for(self, archetype: Archetype) -> list[BlockId]: ...
       def resolve(self, block_id: BlockId) -> BlockSpec: ...
       def transitions_allowed(self) -> list[TransitionId]: ...
   ```
   `allowed_for` uses a static `ARCHETYPE_BLOCK_POLICY: dict[Archetype, set[Category]]` defined at top of the file — data, not code, so it's diff-friendly.

10. **JSON schema stability.**
    - `SceneGraph.model_json_schema()` must be deterministic across runs. Pin field ordering by using explicit `Field(..., json_schema_extra={"order": N})` only if needed; Pydantic v2 is stable by default with stable field insertion order — document this assumption.
    - Export `SCHEMA_HASH = sha256(json.dumps(SceneGraph.model_json_schema(), sort_keys=True))` as a module-level constant. Provider cache keys incorporate it (see 02).

11. **`framecraft/music.py` — US-016 validator.**
    ```python
    def validate_music(path: Path, scene_graph_duration: float) -> Path:
        # extension ∈ {.mp3, .wav, .m4a}
        # duration ≥ scene_graph_duration (probe via ffprobe)
        # returns canonical path; raises ValueError with actionable message
    ```
    Uses `subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])`. If `ffprobe` missing → raise `RuntimeError` pointing at `framecraft doctor`.

12. **Public API surface** (`framecraft/__init__.py`).
    Re-export `SceneGraph`, `Brief`, `Aspect`, `Mood`, `Archetype`, `REGISTRY`, `BlockRegistry`. Nothing else.

## Testing strategy

- **Unit (`tests/test_schema.py`).** Six invalid graphs, each asserts a distinct `ValidationError` path:
  1. Duration mismatch (sum(scenes) ≠ graph duration).
  2. Unknown `block_id`.
  3. Non-decreasing `scene.start` violated.
  4. `canvas` ≠ `aspect.dimensions`.
  5. Bad hex in `Palette.primary`.
  6. Transition with non-adjacent scenes.
- **Unit (`tests/test_registry.py`).** Registry discovery finds all blocks; `allowed_for` respects `ARCHETYPE_BLOCK_POLICY`; a spec with mismatched provenance fields fails at module import (subprocess-based test to observe the ImportError).
- **Unit (`tests/test_music.py`).** Mock `ffprobe` output; assert extension rejection, duration rejection, happy path.
- **Schema stability.** `test_schema_hash_stable` pickles `SCHEMA_HASH` on first run, fails if it ever changes without a deliberate update — guards the provider cache.

## Acceptance (PRD bullets closed)

- US-002: all AC bullets.
- US-003: all AC bullets except `catalog` CLI command (06a) and template-authoring rule ("adding a new block requires only a new file" — depends on 04 too).
- US-016 validator bullet only; injection is 04; CLI flag is 06b.
- FR-12 (partial — `scene.polished` field defined here; write-back in 04).
- FR-13 (schema fields exist; mechanism in 04).
- FR-14 (Aspect dimensions here; emission in 04).

## Open questions

- **OQ-F1.1** Should `SceneGraph` carry a `notes: str` freeform field for Director commentary (useful for debugging, easy to ignore)? *Leaning yes — cheap, forward-compatible.*
- **OQ-F1.2** Do we allow negative `data-start` (pre-roll) on scenes? PRD is silent; existing projects use ≥0 only. *Leaning: reject in validator; re-open if a block needs it.*

## Verification

```bash
# Once 01 is implemented:
python -c "from framecraft import SceneGraph; print(SceneGraph.model_json_schema()['title'])"
# → "SceneGraph"

python -c "from framecraft.registry import REGISTRY, BlockRegistry; from framecraft import Archetype; print(sorted(BlockRegistry(REGISTRY, {}).allowed_for(Archetype.PRODUCT_PROMO)))"
# → ['app-showcase', 'end-card', 'gradient-bg', 'grain-overlay', 'logo-outro', 'title-card', ...]

pytest tests/test_schema.py tests/test_registry.py tests/test_music.py -v
```
