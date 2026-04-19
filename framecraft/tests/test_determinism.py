"""Determinism contract — assembling the same plan twice produces identical bytes.

See §5 of `.claude/plans/00-plan-index.md` and `.claude/plans/04-assembler.md`.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from framecraft.assembler import Assembler
from framecraft.registry import default_registry
from framecraft.schema import SceneGraph

FIXTURE_PLAN = Path(__file__).parent / "fixtures" / "plans" / "product-promo.json"


@pytest.fixture
def plan() -> SceneGraph:
    return SceneGraph.model_validate_json(FIXTURE_PLAN.read_text())


@pytest.fixture
def stub_provider():
    m = MagicMock()
    m.name = "stub"
    return m


def _non_internal_files(out_dir: Path) -> list[Path]:
    return sorted(
        p for p in out_dir.rglob("*")
        if p.is_file() and ".framecraft" not in p.parts
    )


def test_assemble_twice_identical_bytes(plan, stub_provider, tmp_path):
    registry = default_registry()
    assembler = Assembler(registry, stub_provider)

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()

    assembler.assemble(plan, out1, project_name="DetermTest", project_id="det-001")
    assembler.assemble(plan, out2, project_name="DetermTest", project_id="det-001")

    files1 = _non_internal_files(out1)
    files2 = _non_internal_files(out2)

    rels1 = [f.relative_to(out1) for f in files1]
    rels2 = [f.relative_to(out2) for f in files2]
    assert rels1 == rels2, f"File sets differ: {set(rels1) ^ set(rels2)}"

    for f1, f2 in zip(files1, files2):
        assert f1.read_bytes() == f2.read_bytes(), \
            f"{f1.relative_to(out1)} differs between runs"


def test_duration_serialized_to_3_decimals(plan, stub_provider, tmp_path):
    """data-duration on every element uses exactly 3 decimal places (no float jitter)."""
    registry = default_registry()
    assembler = Assembler(registry, stub_provider)

    out = tmp_path / "out"
    out.mkdir()
    assembler.assemble(plan, out)

    for comp in sorted((out / "compositions").glob("*.html")):
        for match in re.finditer(r'data-duration="([^"]+)"', comp.read_text()):
            d = match.group(1)
            assert "." in d, f"duration {d!r} missing decimal point in {comp.name}"
            decimals = d.split(".")[1]
            assert len(decimals) == 3, \
                f"duration {d!r} in {comp.name} has {len(decimals)} decimals (want 3)"


def test_scene_starts_absolute_in_index(plan, stub_provider, tmp_path):
    """data-start values in index.html are absolute (OQ-3 resolution)."""
    registry = default_registry()
    assembler = Assembler(registry, stub_provider)

    out = tmp_path / "out"
    out.mkdir()
    assembler.assemble(plan, out)

    root = (out / "index.html").read_text()
    starts = re.findall(r'data-start="([^"]+)"', root)
    # Root div starts at 0; scene 0 starts at 0; scene 1 starts at 5
    assert "0.000" in starts
    assert "5.000" in starts
    # No relative syntax (no "+", no expressions)
    for s in starts:
        assert "+" not in s
        assert not s.startswith("=")


def test_plan_json_round_trips(plan, stub_provider, tmp_path):
    """Written plan.json is a valid SceneGraph with the same scenes."""
    registry = default_registry()
    assembler = Assembler(registry, stub_provider)

    out = tmp_path / "out"
    out.mkdir()
    assembler.assemble(plan, out)

    written = SceneGraph.model_validate_json((out / "plan.json").read_text())
    assert len(written.scenes) == len(plan.scenes)
    for orig, writ in zip(plan.scenes, written.scenes):
        assert orig.block_id == writ.block_id
        assert orig.duration == writ.duration
