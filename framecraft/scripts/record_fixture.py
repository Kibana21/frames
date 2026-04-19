#!/usr/bin/env python3
"""Record real provider responses as stub fixture files.

Usage:
    python scripts/record_fixture.py --provider gemini --situation narrative
    python scripts/record_fixture.py --provider anthropic --situation product-promo
    python scripts/record_fixture.py --provider gemini --all

Requires API keys:
    GEMINI_API_KEY (or GOOGLE_API_KEY) for --provider gemini
    ANTHROPIC_API_KEY for --provider anthropic

Fixture files are written to tests/fixtures/llm/{provider}/<hash>.json.
Each file contains the provider response JSON that the StubProvider can replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the package is importable when run from the repo root.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

GOLDENS_DIR = ROOT / "tests" / "goldens"
FIXTURE_BASE = ROOT / "tests" / "fixtures" / "llm"

SITUATIONS = ["narrative", "product-promo", "data-explainer"]


def record_situation(situation_name: str, provider_name: str) -> None:
    from framecraft.director import Director
    from framecraft.providers import make_provider
    from framecraft.providers.stub import StubProvider
    from framecraft.registry import default_registry
    from framecraft.schema import Brief, Aspect

    situation_file = GOLDENS_DIR / situation_name / "situation.txt"
    if not situation_file.exists():
        print(f"[error] {situation_file} not found", file=sys.stderr)
        sys.exit(1)

    brief_text = situation_file.read_text(encoding="utf-8").strip()
    print(f"Recording: provider={provider_name!r}, situation={situation_name!r}")
    print(f"  Brief: {brief_text[:80]}...")

    # Make the real provider.
    real_provider = make_provider(provider_name)

    # Fixture dir for this provider.
    provider_family = provider_name.split(":")[0]
    fixture_dir = FIXTURE_BASE / provider_family
    fixture_dir.mkdir(parents=True, exist_ok=True)

    # Wrap the real provider to intercept complete() calls and record them.
    recorded: list[dict] = []

    class RecordingProvider:
        name = real_provider.name

        def complete(self, messages, *, system, schema=None, cache_segments=None, model=None):
            resp = real_provider.complete(
                messages, system=system, schema=schema,
                cache_segments=cache_segments, model=model,
            )
            recorded.append({
                "system": system,
                "messages": messages,
                "schema": schema,
                "response": {
                    "text": resp.text,
                    "parsed": resp.parsed,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "cache_read_tokens": resp.cache_read_tokens,
                    "cache_write_tokens": resp.cache_write_tokens,
                    "provider": resp.provider,
                    "model": resp.model,
                    "elapsed_ms": resp.elapsed_ms,
                },
            })
            return resp

    recording_provider = RecordingProvider()

    # Run the Director with the recording provider.
    registry = default_registry()
    director = Director(recording_provider, registry)
    brief = Brief(situation=brief_text)

    try:
        plan = director.plan(brief)
    except Exception as e:
        print(f"[error] Director failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Write each recorded interaction as a fixture file.
    stub = StubProvider(fixture_dir, f"stub:{provider_family}")
    written = 0
    for rec in recorded:
        key = stub._fixture_key(
            messages=rec["messages"],
            system=rec["system"],
            schema=rec["schema"],
        )
        fixture_path = fixture_dir / f"{key}.json"
        fixture_data = {
            "provider": f"stub:{provider_family}",
            "model": rec["response"]["model"],
            **{k: v for k, v in rec["response"].items() if k not in ("provider", "model")},
        }
        fixture_path.write_text(json.dumps(fixture_data, indent=2, ensure_ascii=False),
                                encoding="utf-8")
        print(f"  → {fixture_path.name}")
        written += 1

    print(f"Recorded {written} fixture(s) to {fixture_dir}")
    print(f"  SceneGraph: {plan.archetype.value}, {len(plan.scenes)} scenes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record stub LLM fixtures from real providers.")
    parser.add_argument("--provider", choices=["gemini", "anthropic"], required=True)
    parser.add_argument("--situation", choices=SITUATIONS + ["all"], default="all")
    args = parser.parse_args()

    situations = SITUATIONS if args.situation == "all" else [args.situation]
    for s in situations:
        record_situation(s, args.provider)


if __name__ == "__main__":
    main()
