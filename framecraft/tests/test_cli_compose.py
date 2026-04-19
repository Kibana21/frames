"""M0 CLI integration test — compose --dry-run produces a valid project."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from framecraft.cli import app

runner = CliRunner()


@pytest.fixture
def tmp_out(tmp_path: Path) -> Path:
    """Fresh output dir, removed before the test and after if left behind."""
    out = tmp_path / "fc-out"
    if out.exists():
        shutil.rmtree(out)
    return out


def _npx_available() -> bool:
    return shutil.which("npx") is not None


@pytest.mark.skipif(not _npx_available(), reason="npx not on PATH")
def test_compose_dry_run_produces_valid_project(tmp_out: Path) -> None:
    result = runner.invoke(
        app,
        [
            "compose",
            "A test situation for a minimal product promo",
            "--dry-run",
            "--out",
            str(tmp_out),
            "--duration",
            "8",
        ],
    )
    assert result.exit_code == 0, result.output

    assert (tmp_out / "index.html").is_file()
    assert (tmp_out / "plan.json").is_file()
    assert (tmp_out / "hyperframes.json").is_file()
    assert (tmp_out / "meta.json").is_file()

    comp_files = sorted((tmp_out / "compositions").glob("*.html"))
    assert len(comp_files) == 2
    assert comp_files[0].name.startswith("scene-00-")
    assert comp_files[1].name.startswith("scene-01-")

    # Every comp file wraps in a <template>
    for f in comp_files:
        text = f.read_text()
        assert text.startswith("<template id=")
        assert text.rstrip().endswith("</template>")
        assert 'data-composition-id="scene-' in text
        assert 'window.__timelines[' in text

    # Root has the main timeline registration
    root = (tmp_out / "index.html").read_text()
    assert 'window.__timelines["main"]' in root
    assert 'data-composition-id="main"' in root


@pytest.mark.skipif(not _npx_available(), reason="npx not on PATH")
def test_compose_without_dry_run_requires_provider_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --dry-run the Director runs; missing API keys should surface
    as a clean exit-1 with an actionable message, not a stack trace."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("FRAMECRAFT_PROVIDER", "gemini")
    # Run from a dir without key.json so service-account auto-discovery is skipped.
    monkeypatch.chdir(tmp_path)

    out = tmp_path / "fc-need-key"
    result = runner.invoke(
        app, ["compose", "a test situation", "--out", str(out), "--duration", "8"]
    )
    assert result.exit_code == 1


def test_doctor_runs() -> None:
    result = runner.invoke(app, ["doctor"])
    # Doctor can legitimately exit 0 or 1 depending on env; the test just
    # confirms it doesn't crash.
    assert result.exit_code in (0, 1)
    assert "framecraft doctor" in result.output


def test_catalog_lists_blocks() -> None:
    result = runner.invoke(app, ["catalog"])
    assert result.exit_code == 0
    assert "title-card" in result.output
    assert "end-card" in result.output


def test_catalog_json() -> None:
    result = runner.invoke(app, ["catalog", "--json"])
    assert result.exit_code == 0
    import json

    data = json.loads(result.output)
    assert "title-card" in data
    assert data["title-card"]["provenance"] == "native"
