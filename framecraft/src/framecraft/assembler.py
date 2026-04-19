"""Assembler — SceneGraph → HTML. See `.claude/plans/04-assembler.md`."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from framecraft.providers.base import LLMProvider
from framecraft.providers.stub import StubProvider
from framecraft.registry import BlockRegistry
from framecraft.rendering.audio import copy_audio_asset
from framecraft.rendering.catalog import (
    CatalogHashError,
    CatalogSlotError,
    inject_slots,
    install_catalog_block,
)
from framecraft.rendering.ids import file_name
from framecraft.rendering.llm_author import (
    AuthorRequest,
    LLMAuthorError,
    author_scene_html,
)
from framecraft.rendering.root import write_root
from framecraft.schema import Provenance, Scene, SceneGraph
from framecraft.trace import AssemblerSceneTrace

_log = logging.getLogger("framecraft.assembler")

__all__ = ["Assembler"]

_SceneTrace = AssemblerSceneTrace  # backward-compat alias


class Assembler:
    def __init__(
        self,
        registry: BlockRegistry,
        provider: LLMProvider,
        *,
        full_polish: bool = False,
    ) -> None:
        self.registry = registry
        self.provider = provider
        self.full_polish = full_polish
        self._polish_cache_hits = 0
        self._polish_cache_misses = 0

    def assemble(
        self,
        plan: SceneGraph,
        out_dir: Path,
        *,
        project_name: str = "FrameCraft Project",
        project_id: str = "framecraft-project",
    ) -> None:
        comps = out_dir / "compositions"
        comps.mkdir(parents=True, exist_ok=True)
        traces_dir = out_dir / ".framecraft" / "assembler-traces"
        traces_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot previous plan.json before overwriting (for from-plan diff in M3).
        prior = out_dir / "plan.json"
        if prior.exists():
            (out_dir / ".framecraft" / "last-plan.json").write_text(
                prior.read_text(encoding="utf-8"), encoding="utf-8"
            )

        # Pre-install all unique CATALOG blocks.
        self._install_catalog_blocks(plan, out_dir)

        w, h = plan.canvas
        updated_scenes: list[Scene] = []

        for scene in plan.scenes:
            t0 = time.perf_counter()
            spec = self.registry.resolve(scene.block_id)
            polish_cache: dict[str, str] = dict(scene.polished)

            if spec.provenance is Provenance.NATIVE:
                assert spec.template is not None
                rendered, hits, misses = self._render_native(
                    scene, spec, plan, polish_cache, w, h
                )
            else:
                installed_path = self._catalog_primary(spec, out_dir)
                installed_html = installed_path.read_text(encoding="utf-8")
                rendered, hits, misses = inject_slots(
                    installed_html,
                    spec.slots,
                    scene.block_props,
                    self.provider,
                    polish_cache,
                )

            self._polish_cache_hits += hits
            self._polish_cache_misses += misses
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            (comps / file_name(scene.index, scene.block_id)).write_text(
                rendered, encoding="utf-8"
            )
            updated_scenes.append(scene.model_copy(update={"polished": polish_cache}))

            # Write per-scene trace only when provider was actually called.
            if misses > 0:
                trace = _SceneTrace(
                    scene_index=scene.index,
                    block_id=scene.block_id.value,
                    provenance=spec.provenance.value,
                    polish_cache_hits=hits,
                    polish_cache_misses=misses,
                    elapsed_ms=elapsed_ms,
                )
                (traces_dir / f"scene-{scene.index:02d}.json").write_text(
                    trace.model_dump_json(indent=2), encoding="utf-8"
                )

        # Copy audio asset if specified (HTML element emitted by write_root).
        if plan.brief.music_path is not None:
            copy_audio_asset(plan.brief.music_path, out_dir)

        write_root(out_dir, plan, project_name=project_name, project_id=project_id)

        # Persist plan.json with polished values written back.
        plan_out = plan.model_copy(update={"scenes": updated_scenes})
        (out_dir / "plan.json").write_text(
            plan_out.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
        )

    def repair(
        self,
        out_dir: Path,
        plan: SceneGraph,
        errors_only: list[dict],
    ) -> None:
        """Re-render only scenes with LLM_REPAIRABLE lint findings.

        For each unique file in findings: re-renders that scene from its
        block_props (no Director call). Writes a repair trace under
        .framecraft/assembler-traces/repair-<timestamp>.json.
        """
        if not errors_only:
            return

        comps = out_dir / "compositions"
        w, h = plan.canvas

        scenes_by_file: dict[str, Scene] = {
            file_name(s.index, s.block_id): s for s in plan.scenes
        }

        repaired: list[str] = []
        for finding in errors_only:
            fname = finding.get("file", "")
            if fname not in scenes_by_file or fname in repaired:
                continue
            scene = scenes_by_file[fname]
            spec = self.registry.resolve(scene.block_id)
            if spec.provenance is Provenance.NATIVE:
                assert spec.template is not None
                rendered = spec.template(scene.block_props, scene.index, w, h, scene.duration)
                (comps / fname).write_text(rendered, encoding="utf-8")
                repaired.append(fname)

        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        trace = {
            "timestamp": ts,
            "repaired_files": repaired,
            "findings_count": len(errors_only),
        }
        traces_dir = out_dir / ".framecraft" / "assembler-traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        (traces_dir / f"repair-{ts}.json").write_text(
            json.dumps(trace, indent=2), encoding="utf-8"
        )

    # --- helpers -------------------------------------------------------------

    def _render_native(
        self,
        scene: Scene,
        spec,
        plan: SceneGraph,
        polish_cache: dict[str, str],
        w: int,
        h: int,
    ) -> tuple[str, int, int]:
        """Render a native block. Returns (html, cache_hits, cache_misses).

        Routing:
          1. `polish_cache["html"]` hit → reuse (1 hit, 0 miss).
          2. `full_polish=True` + real provider → LLM-author; cache on success
             (0 hit, 1 miss). On validation failure, fall back to native.
          3. Otherwise → Python template (0, 0).
        """
        cached = polish_cache.get("html")
        if cached:
            return cached, 1, 0

        if self.full_polish and not isinstance(self.provider, StubProvider):
            try:
                html = author_scene_html(
                    self.provider,
                    AuthorRequest(
                        scene_index=scene.index,
                        block_id=scene.block_id,
                        props=scene.block_props,
                        duration=scene.duration,
                        canvas_w=w,
                        canvas_h=h,
                        mood=plan.brief.mood.value if plan.brief.mood else None,
                        archetype=plan.archetype.value,
                        aspect=plan.brief.aspect.value,
                        style_seed=plan.brief.style_seed,
                    ),
                )
                polish_cache["html"] = html
                return html, 0, 1
            except LLMAuthorError as e:
                _log.warning(
                    "llm_author failed for scene %d (%s): %s — falling back to native template",
                    scene.index,
                    scene.block_id.value,
                    e,
                )

        rendered = spec.template(scene.block_props, scene.index, w, h, scene.duration)
        return rendered, 0, 0

    def _install_catalog_blocks(self, plan: SceneGraph, out_dir: Path) -> None:
        seen: set[str] = set()
        for scene in plan.scenes:
            spec = self.registry.resolve(scene.block_id)
            if spec.provenance is not Provenance.CATALOG:
                continue
            key = spec.catalog_id or scene.block_id.value
            if key not in seen:
                install_catalog_block(
                    spec.catalog_id or key,
                    spec.catalog_version or "latest",
                    spec.catalog_hash or "",
                    out_dir,
                )
                seen.add(key)

    def _catalog_primary(self, spec, out_dir: Path) -> Path:
        manifest_path = (
            out_dir / ".framecraft" / "installed" / f"{spec.catalog_id}.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return out_dir / manifest["primary_file"]
