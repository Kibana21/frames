"""Prompt assets shipped in the wheel. See `.claude/plans/08-primer-snapshot.md`."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

Role = Literal["director", "assembler"]

_PROMPTS_ROOT = Path(__file__).parent
_PRIMER_FILE = _PROMPTS_ROOT / "primer.md"


@lru_cache(maxsize=1)
def load_primer() -> str:
    """Return the packaged Hyperframes primer.

    M0/M1 ship a short placeholder. Run `python scripts/snapshot_primer.py`
    to generate the real thing (08-primer-snapshot.md).
    """
    override = os.environ.get("FRAMECRAFT_PRIMER_PATH")
    if override:
        return Path(override).read_text(encoding="utf-8")
    return _PRIMER_FILE.read_text(encoding="utf-8")


def load_common(role: Role) -> str:
    """Shared, provider-agnostic role body. Goes into `cache_segments`."""
    return (_PROMPTS_ROOT / "common" / f"{role}.md").read_text(encoding="utf-8")


def load_provider_framing(provider: str, role: Role) -> str:
    """Per-provider trailing instruction — NOT cached; small and may mutate.

    Unknown providers fall back to the Gemini framing so a newcomer adapter
    still gets something reasonable instead of a FileNotFoundError.
    """
    family = provider.split(":")[-1]
    path = _PROMPTS_ROOT / family / f"{role}.md"
    if not path.exists():
        path = _PROMPTS_ROOT / "gemini" / f"{role}.md"
    return path.read_text(encoding="utf-8")
