"""Unit tests for Assembler. See `.claude/plans/04-assembler.md` §Testing strategy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from framecraft.assembler import Assembler
from framecraft.blocks._spec import SlotSpec
from framecraft.registry import default_registry
from framecraft.rendering.catalog import CatalogSlotError, inject_slots
from framecraft.rendering.ids import file_name
from framecraft.schema import SceneGraph

FIXTURE_PLAN = Path(__file__).parent / "fixtures" / "plans" / "product-promo.json"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def plan() -> SceneGraph:
    return SceneGraph.model_validate_json(FIXTURE_PLAN.read_text())


@pytest.fixture
def stub_provider():
    m = MagicMock()
    m.name = "stub"
    return m


@pytest.fixture
def registry():
    return default_registry()


@pytest.fixture
def assembler(registry, stub_provider):
    return Assembler(registry, stub_provider)


@pytest.fixture
def out(tmp_path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# NATIVE happy path
# ---------------------------------------------------------------------------


def test_native_title_card_renders(plan, assembler, out):
    assembler.assemble(plan, out, project_name="Test", project_id="test-001")

    comp_files = sorted((out / "compositions").glob("*.html"))
    assert len(comp_files) == 2
    assert comp_files[0].name == "scene-00-title-card.html"
    assert comp_files[1].name == "scene-01-end-card.html"

    tc = comp_files[0].read_text()
    assert tc.startswith('<template id="scene-00-title-card-template">')
    assert 'data-composition-id="scene-00-title-card"' in tc
    assert 'data-width="1920"' in tc
    assert 'data-height="1080"' in tc
    assert 'data-duration="5.000"' in tc
    assert 'window.__timelines["scene-00-title-card"]' in tc
    assert tc.rstrip().endswith("</template>")
    assert "Introducing Aria" in tc


def test_assemble_writes_root_files(plan, assembler, out):
    assembler.assemble(plan, out, project_name="Promo", project_id="promo-001")

    assert (out / "index.html").is_file()
    assert (out / "meta.json").is_file()
    assert (out / "plan.json").is_file()

    meta = json.loads((out / "meta.json").read_text())
    assert meta["id"] == "promo-001"
    assert meta["name"] == "Promo"
    assert meta["aspect"] == "16:9"
    assert meta["duration"] == 8.0


def test_assemble_index_html_has_scene_placeholders(plan, assembler, out):
    assembler.assemble(plan, out)

    root = (out / "index.html").read_text()
    assert 'data-composition-id="main"' in root
    assert 'window.__timelines["main"]' in root
    assert 'data-composition-src="compositions/scene-00-title-card.html"' in root
    assert 'data-composition-src="compositions/scene-01-end-card.html"' in root
    assert 'data-start="0.000"' in root
    assert 'data-start="5.000"' in root


def test_assemble_snapshots_prior_plan(plan, assembler, out):
    prior_text = '{"version":1,"note":"prior"}'
    (out / "plan.json").write_text(prior_text, encoding="utf-8")

    assembler.assemble(plan, out)

    last = (out / ".framecraft" / "last-plan.json").read_text()
    assert last == prior_text


def test_plan_json_written_back(plan, assembler, out):
    assembler.assemble(plan, out)
    written = json.loads((out / "plan.json").read_text())
    assert written["version"] == 1
    assert len(written["scenes"]) == 2


# ---------------------------------------------------------------------------
# Polish cache — tested via inject_slots (CATALOG path)
# ---------------------------------------------------------------------------


def test_polish_cache_miss_calls_provider():
    call_count = 0

    class CountingProvider:
        name = "stub"

        def complete(self, *a, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            r.text = "Polished value"
            return r

    html = '<div><span id="hl">original</span></div>'
    slots = {"headline": SlotSpec(kind="text", selector="#hl", target="", llm_polish=True)}
    cache: dict[str, str] = {}

    result, hits, misses = inject_slots(
        html, slots, {"headline": "original"}, CountingProvider(), cache
    )

    assert call_count == 1
    assert misses == 1
    assert hits == 0
    assert "Polished value" in result
    assert len(cache) == 1


def test_polish_cache_hit_no_provider_call():
    call_count = 0

    class CountingProvider:
        name = "stub"

        def complete(self, *a, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            r.text = "Should not be called"
            return r

    raw = "original"
    ck = f"headline::{hashlib.sha256(raw.encode()).hexdigest()[:12]}"
    pre_populated: dict[str, str] = {ck: "Pre-polished"}

    html = '<div><span id="hl">original</span></div>'
    slots = {"headline": SlotSpec(kind="text", selector="#hl", target="", llm_polish=True)}

    result, hits, misses = inject_slots(
        html, slots, {"headline": raw}, CountingProvider(), pre_populated
    )

    assert call_count == 0
    assert hits == 1
    assert misses == 0
    assert "Pre-polished" in result


def test_polish_cache_miss_when_input_changes():
    calls: list[str] = []

    class RecordingProvider:
        name = "stub"

        def complete(self, messages, **kw):
            calls.append(messages[0]["content"])
            r = MagicMock()
            r.text = f"polished:{calls[-1][-5:]}"
            return r

    slots = {"headline": SlotSpec(kind="text", selector="#hl", target="", llm_polish=True)}
    cache: dict[str, str] = {}
    html_tpl = '<div><span id="hl">{}</span></div>'

    inject_slots(html_tpl.format("first"), slots, {"headline": "first"}, RecordingProvider(), cache)
    inject_slots(html_tpl.format("second"), slots, {"headline": "second"}, RecordingProvider(), cache)

    assert len(calls) == 2  # different inputs → two misses


def test_catalog_slot_missing_selector_raises():
    html = "<div><span id='other'>text</span></div>"
    slots = {"title": SlotSpec(kind="text", selector="#nonexistent", target="")}
    provider = MagicMock()
    provider.name = "stub"

    with pytest.raises(CatalogSlotError, match="selector `#nonexistent`"):
        inject_slots(html, slots, {"title": "value"}, provider, {})


def test_catalog_slot_css_var():
    html = '<div id="box" style="color: red"></div>'
    slots = {"bg": SlotSpec(kind="css_var", selector="#box", target="--bg-color")}
    provider = MagicMock()
    provider.name = "stub"

    result, _, _ = inject_slots(html, slots, {"bg": "#FF0000"}, provider, {})
    assert "--bg-color: #FF0000" in result


def test_catalog_slot_attr():
    html = '<img id="hero" src="">'
    slots = {"img": SlotSpec(kind="attr", selector="#hero", target="src")}
    provider = MagicMock()
    provider.name = "stub"

    result, _, _ = inject_slots(html, slots, {"img": "assets/hero.png"}, provider, {})
    assert 'src="assets/hero.png"' in result


# ---------------------------------------------------------------------------
# Repair path
# ---------------------------------------------------------------------------


def test_repair_rerenders_affected_scene(plan, assembler, out):
    assembler.assemble(plan, out)

    tc_path = out / "compositions" / "scene-00-title-card.html"
    tc_path.write_text("corrupted", encoding="utf-8")

    errors = [{"file": "scene-00-title-card.html", "rule": "missing-template-wrap"}]
    assembler.repair(out, plan, errors)

    restored = tc_path.read_text()
    assert restored.startswith("<template")
    assert "Introducing Aria" in restored

    traces = list((out / ".framecraft" / "assembler-traces").glob("repair-*.json"))
    assert len(traces) == 1
    trace = json.loads(traces[0].read_text())
    assert "scene-00-title-card.html" in trace["repaired_files"]


def test_repair_noop_on_empty_findings(plan, assembler, out):
    assembler.assemble(plan, out)

    tc_path = out / "compositions" / "scene-00-title-card.html"
    original = tc_path.read_text()

    assembler.repair(out, plan, [])

    assert tc_path.read_text() == original
    traces = list((out / ".framecraft" / "assembler-traces").glob("repair-*.json"))
    assert len(traces) == 0


def test_repair_only_touches_listed_files(plan, assembler, out):
    assembler.assemble(plan, out)

    ec_path = out / "compositions" / "scene-01-end-card.html"
    ec_original = ec_path.read_text()

    tc_path = out / "compositions" / "scene-00-title-card.html"
    tc_path.write_text("corrupted", encoding="utf-8")

    assembler.repair(out, plan, [{"file": "scene-00-title-card.html", "rule": "x"}])

    # end-card untouched
    assert ec_path.read_text() == ec_original
