"""Pytest config for framecraft tests."""

from __future__ import annotations

import pytest


def pytest_configure(config):  # type: ignore[no-untyped-def]
    """Register markers so pytest doesn't warn when invoked from the repo root."""
    config.addinivalue_line(
        "markers", "llm: tests that require a real LLM provider API key"
    )
    config.addinivalue_line(
        "markers", "golden: golden-file snapshot tests"
    )


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    """Skip @pytest.mark.llm tests unless --runllm is passed."""
    if config.getoption("--runllm"):
        return
    skip_llm = pytest.mark.skip(reason="needs --runllm to hit live provider APIs")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip_llm)


def pytest_addoption(parser):  # type: ignore[no-untyped-def]
    parser.addoption("--runllm", action="store_true", default=False,
                     help="Run @llm tests against live provider APIs.")
    parser.addoption("--update-goldens", action="store_true", default=False,
                     help="Overwrite golden expected/ trees with actual output on mismatch.")
    parser.addoption("--update-fixtures", action="store_true", default=False,
                     help="Re-record stub fixtures from live providers (requires --runllm).")


@pytest.fixture
def update_goldens(request) -> bool:  # type: ignore[no-untyped-def]
    return bool(request.config.getoption("--update-goldens"))


@pytest.fixture
def update_fixtures(request) -> bool:  # type: ignore[no-untyped-def]
    return bool(request.config.getoption("--update-fixtures"))
