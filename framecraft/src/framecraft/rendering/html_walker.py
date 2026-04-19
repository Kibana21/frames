"""Deterministic HTML DOM manipulation via BeautifulSoup4.

Used by the CATALOG slot-injection path. Stable ordering (alphabetical
CSS property sort) ensures byte-identical output for identical inputs —
required by the determinism contract (§5 of 00-plan-index.md).
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag


def parse(html: str) -> BeautifulSoup:
    """Parse HTML with html.parser (stdlib, no external C deps)."""
    return BeautifulSoup(html, "html.parser")


def select_one(soup: BeautifulSoup, selector: str) -> Tag | None:
    """Return first matching Tag or None."""
    result = soup.select_one(selector)
    if result is None or not isinstance(result, Tag):
        return None
    return result


def merge_style(existing: str, additions: dict[str, str]) -> str:
    """Merge CSS property declarations into an inline style string.

    Existing declarations for the same property are overwritten. Output is
    sorted alphabetically so repeated merge calls produce identical strings.
    """
    props: dict[str, str] = {}
    for part in (existing or "").split(";"):
        part = part.strip()
        if ":" in part:
            k, _, v = part.partition(":")
            props[k.strip()] = v.strip()
    props.update(additions)
    return "; ".join(f"{k}: {v}" for k, v in sorted(props.items()) if v)


def serialize(soup: BeautifulSoup) -> str:
    """Serialize parsed DOM back to a string."""
    return str(soup)
