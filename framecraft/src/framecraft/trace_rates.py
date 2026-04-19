"""Published token rates for cost estimation in RunSummary.

Rates are per 1M tokens (USD). Update when providers change pricing.
Cache-write tokens cost more than cache-read tokens (Anthropic: 25% more;
Gemini: same as input for now).

Keys: (provider_family, model_id) — use provider_family for partial matches.
"""

from __future__ import annotations

# (provider_family, model) → (input_per_1m, output_per_1m, cache_read_per_1m, cache_write_per_1m)
_RATES: dict[tuple[str, str], tuple[float, float, float, float]] = {
    # Gemini 2.5 Pro (context <200K)
    ("gemini", "gemini-2.5-pro"):     (1.25,  10.00, 0.31, 1.25),
    # Gemini 2.5 Flash
    ("gemini", "gemini-2.5-flash"):   (0.15,  0.60,  0.037, 0.15),
    # Claude Opus 4.7
    ("anthropic", "claude-opus-4-7"): (15.00, 75.00, 1.50, 18.75),
    # Claude Sonnet 4.6
    ("anthropic", "claude-sonnet-4-6"): (3.00, 15.00, 0.30, 3.75),
    # Claude Haiku 4.5
    ("anthropic", "claude-haiku-4-5-20251001"): (0.80, 4.00, 0.08, 1.00),
    # Stub / unknown
    ("stub", "stub"):                 (0.0, 0.0, 0.0, 0.0),
    ("stub", "stub-director"):        (0.0, 0.0, 0.0, 0.0),
    ("stub", "stub-assembler"):       (0.0, 0.0, 0.0, 0.0),
}

_FALLBACK = (1.0, 5.0, 0.1, 1.0)  # conservative unknown-model estimate


def cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> float:
    family = provider.split(":")[0].lower()
    rate = _RATES.get((family, model)) or _RATES.get((family, "stub")) or _FALLBACK
    inp, out, cr, cw = rate
    total = (
        input_tokens * inp
        + output_tokens * out
        + cache_read_tokens * cr
        + cache_write_tokens * cw
    ) / 1_000_000
    return round(total, 6)
