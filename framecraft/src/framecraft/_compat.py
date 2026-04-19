"""Pinned version floors and expected scaffold outputs.

Bumping the floor is a deliberate act — update alongside the changelog entry
that explains why.
"""

from __future__ import annotations

# Hyperframes CLI minimum version. Current installed in dev env: 0.4.4.
HYPERFRAMES_VERSION_FLOOR = "0.4.0"

# Files we expect `npx hyperframes init --example blank` to leave behind.
# Extra files from upstream are fine; missing files indicate scaffold drift.
EXPECTED_INIT_FILES: frozenset[str] = frozenset({
    "hyperframes.json",
})

# The "blank" example does not ship with any compositions/ directory; the
# Assembler creates it during assemble(). Keep this empty unless upstream
# changes the blank example.
EXPECTED_INIT_DIRS: frozenset[str] = frozenset()
