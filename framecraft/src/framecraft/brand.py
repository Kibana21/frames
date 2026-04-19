"""Branding flag parsing — `--logo`, `--palette`, `--font` → BrandKit."""

from __future__ import annotations

from pathlib import Path

from framecraft.exit_codes import ExitCode, FrameCraftExit
from framecraft.schema import BrandKit, Palette, Typography


def build_brand_kit(
    logo: Path | None,
    palette: str | None,
    font: str | None,
) -> BrandKit | None:
    if logo is None and palette is None and font is None:
        return None

    resolved_palette = _parse_palette(palette) if palette else None
    resolved_typography = Typography(headline=font, body=font) if font else None

    if logo is not None and not logo.exists():
        raise FrameCraftExit(ExitCode.USAGE, f"--logo path not found: {logo}")

    return BrandKit(
        logo_path=logo,
        palette=resolved_palette,
        typography=resolved_typography,
    )


def _parse_palette(spec: str) -> Palette:
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if len(parts) != 3:
        raise FrameCraftExit(
            ExitCode.USAGE,
            f'--palette expects "primary,bg,accent" (3 hex values); got {spec!r}',
        )
    try:
        return Palette(primary=parts[0], bg=parts[1], accent=parts[2])
    except ValueError as e:
        raise FrameCraftExit(ExitCode.USAGE, f"--palette: {e}") from e
