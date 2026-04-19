"""Observability helpers: always-write guarantee (FR-11) and trace redaction (§7.5).

Both primitives live in `trace.py` (first emitter owns the definition);
this module re-exports them and documents the contract so callers can import
from a single stable location.

§7.5 contract: traces NEVER store raw prompt strings. Always call
`hash_for_trace(s)` and store the hash in a `*_sha256` field.
"""

from __future__ import annotations

# Re-export the two primitives so callers can `from framecraft.observability import …`
from framecraft.trace import always_write as always_write  # noqa: F401
from framecraft.trace import hash_for_trace as hash_for_trace  # noqa: F401

__all__ = ["always_write", "hash_for_trace"]
