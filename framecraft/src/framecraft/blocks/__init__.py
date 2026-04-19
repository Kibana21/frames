"""Block package. All .py files in this directory that don't start with `_`
are discovered at runtime and each must export `SPEC: BlockSpec`.

Discovery lives in `framecraft.registry._bootstrap()` so that adding a new
block is a single-file change (plus a `scripts/gen_block_ids.py` run to
update the `BlockId` enum).
"""
