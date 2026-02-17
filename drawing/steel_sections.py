"""Steel section parser and weight calculator for MEMBER LIST (部材リスト).

Parses Japanese steel section notation from engineering drawings and computes:
- Cross-sectional area (mm²)
- Unit weight (kg/m)
- Lattice truss equivalent weight (kg/m)

Supported notations:
    P-Dφ×t         Pipe (STK)
    □-B×H×t        Square / rectangular tube (STKR)
    L-a×b×t        Angle
    M-d            Round bar
    FB-b×t         Flat bar
    nX-…, D=d, ラチスP-…φ×t, θ=deg   Lattice truss (built-up)
"""

from __future__ import annotations

import math
import re
from enum import Enum

from pydantic import BaseModel, Field

# Steel density: 7850 kg/m³ → 7.85×10⁻³ kg per mm² per meter
STEEL_DENSITY = 7.85e-3


# ────────────────────────────────────────────
# Models
# ────────────────────────────────────────────


class SectionShape(str, Enum):
    PIPE = "pipe"
    SQUARE_TUBE = "square_tube"
    RECT_TUBE = "rect_tube"
    ANGLE = "angle"
    ROUND_BAR = "round_bar"
    FLAT_BAR = "flat_bar"


class SteelSection(BaseModel):
    """Parsed single steel cross-section."""

    shape: SectionShape
    notation: str
    D: float | None = None  # Outer diameter / width (mm)
    H: float | None = None  # Height / second leg (mm)
    t: float | None = None  # Thickness (mm)
    area: float  # mm²
    unit_weight: float  # kg/m


class LatticeTrussSpec(BaseModel):
    """Lattice (built-up) truss member specification."""

    chord: SteelSection
    chord_count: int
    lattice: SteelSection
    depth: float  # D: truss depth (mm)
    angle_deg: float  # θ (degrees)
    chord_weight_per_m: float  # kg/m (all chords combined)
    lattice_weight_per_m: float  # kg/m (equivalent diagonal)
    total_weight_per_m: float  # kg/m


class MemberEntry(BaseModel):
    """One row from the drawing MEMBER LIST."""

    number: str  # ①, ②, …
    name: str  # 主架構材, 繋材, …
    name_en: str = ""
    section_text: str  # Raw notation from drawing
    material: str  # STK400, STKR400, …
    sections: list[SteelSection] = Field(default_factory=list)
    truss: LatticeTrussSpec | None = None
    unit_weight: float  # Final computed kg/m


class MemberCatalog(BaseModel):
    """Complete MEMBER LIST for one drawing."""

    drawing_number: str
    entries: list[MemberEntry]


# ────────────────────────────────────────────
# Area calculators
# ────────────────────────────────────────────


def pipe_area(D: float, t: float) -> float:
    """Pipe: A = π(D-t)t"""
    return math.pi * (D - t) * t


def tube_area(B: float, H: float, t: float) -> float:
    """Square/rectangular tube: A = 2(B+H-2t)t"""
    return 2.0 * (B + H - 2.0 * t) * t


def angle_area(a: float, b: float, t: float) -> float:
    """Angle: A = (a+b-t)t"""
    return (a + b - t) * t


def round_bar_area(d: float) -> float:
    """Round bar: A = πd²/4"""
    return math.pi * d * d / 4.0


def flat_bar_area(b: float, t: float) -> float:
    """Flat bar: A = bt"""
    return b * t


def to_kg_m(area_mm2: float) -> float:
    """Area (mm²) → unit weight (kg/m)."""
    return round(area_mm2 * STEEL_DENSITY, 3)


# ────────────────────────────────────────────
# Section parsers
# ────────────────────────────────────────────

# Regex building blocks for Japanese notation variants
_SEP = r"[×xX]"  # multiplication sign
_DASH = r"[-\u2212\u2013]"  # hyphen / full-width minus / en-dash
_PHI = r"[\u03c6\u03a6\u00f8\u0278]"  # φ Φ ø ɸ


def parse_section(text: str) -> SteelSection | None:
    """Parse a single section notation. Returns None if unrecognized."""
    text = text.strip()
    for fn in (
        _parse_pipe,
        _parse_tube,
        _parse_angle,
        _parse_flat_bar,
        _parse_round_bar,
    ):
        result = fn(text)
        if result:
            return result
    return None


def _parse_pipe(text: str) -> SteelSection | None:
    """Parse: P-42.7φ×2.3t, Ps-42.7φ×2.3t"""
    pattern = rf"Ps?{_DASH}(\d+\.?\d*){_PHI}{_SEP}(\d+\.?\d*)t"
    m = re.search(pattern, text)
    if not m:
        return None
    D, t = float(m.group(1)), float(m.group(2))
    a = pipe_area(D, t)
    return SteelSection(
        shape=SectionShape.PIPE,
        notation=f"P-{D}φ×{t}t",
        D=D,
        t=t,
        area=round(a, 1),
        unit_weight=to_kg_m(a),
    )


def _parse_tube(text: str) -> SteelSection | None:
    """Parse: □-50×50×2.3t, □-125×75×2.3t"""
    pattern = rf"\u25a1{_DASH}(\d+\.?\d*){_SEP}(\d+\.?\d*){_SEP}(\d+\.?\d*)t"
    m = re.search(pattern, text)
    if not m:
        return None
    B, H, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
    a = tube_area(B, H, t)
    shape = SectionShape.SQUARE_TUBE if B == H else SectionShape.RECT_TUBE
    return SteelSection(
        shape=shape,
        notation=f"□-{B}×{H}×{t}t",
        D=B,
        H=H,
        t=t,
        area=round(a, 1),
        unit_weight=to_kg_m(a),
    )


def _parse_angle(text: str) -> SteelSection | None:
    """Parse: L-45×45×3t"""
    pattern = rf"L{_DASH}(\d+\.?\d*){_SEP}(\d+\.?\d*){_SEP}(\d+\.?\d*)t"
    m = re.search(pattern, text)
    if not m:
        return None
    a_val, b, t = float(m.group(1)), float(m.group(2)), float(m.group(3))
    a = angle_area(a_val, b, t)
    return SteelSection(
        shape=SectionShape.ANGLE,
        notation=f"L-{a_val}×{b}×{t}t",
        D=a_val,
        H=b,
        t=t,
        area=round(a, 1),
        unit_weight=to_kg_m(a),
    )


def _parse_round_bar(text: str) -> SteelSection | None:
    """Parse: M12, M16"""
    m = re.search(r"M(\d+\.?\d*)", text)
    if not m:
        return None
    d = float(m.group(1))
    if d > 64:  # Larger than M64 → unlikely a bar
        return None
    a = round_bar_area(d)
    return SteelSection(
        shape=SectionShape.ROUND_BAR,
        notation=f"M{d:.0f}",
        D=d,
        area=round(a, 1),
        unit_weight=to_kg_m(a),
    )


def _parse_flat_bar(text: str) -> SteelSection | None:
    """Parse: FB-44×4.5t"""
    pattern = rf"FB{_DASH}(\d+\.?\d*){_SEP}(\d+\.?\d*)t?"
    m = re.search(pattern, text)
    if not m:
        return None
    b, t = float(m.group(1)), float(m.group(2))
    a = flat_bar_area(b, t)
    return SteelSection(
        shape=SectionShape.FLAT_BAR,
        notation=f"FB-{b}×{t}t",
        D=b,
        t=t,
        area=round(a, 1),
        unit_weight=to_kg_m(a),
    )


# ────────────────────────────────────────────
# Lattice truss calculator
# ────────────────────────────────────────────


def calc_lattice_truss(
    chord: SteelSection,
    chord_count: int,
    lattice: SteelSection,
    depth: float,
    angle_deg: float = 45.0,
) -> LatticeTrussSpec:
    """Calculate equivalent kg/m for a lattice truss.

    Formula:
        chord_kg_m    = chord_count × chord.unit_weight
        lattice_kg_m  = lattice.unit_weight / cos(θ)
                        (accounts for diagonal length & spacing)
        total_kg_m    = chord_kg_m + lattice_kg_m
    """
    chord_w = chord_count * chord.unit_weight
    angle_rad = math.radians(angle_deg)
    lattice_w = lattice.unit_weight / math.cos(angle_rad)
    total_w = chord_w + lattice_w
    return LatticeTrussSpec(
        chord=chord,
        chord_count=chord_count,
        lattice=lattice,
        depth=depth,
        angle_deg=angle_deg,
        chord_weight_per_m=round(chord_w, 3),
        lattice_weight_per_m=round(lattice_w, 3),
        total_weight_per_m=round(total_w, 3),
    )


# ────────────────────────────────────────────
# Member entry parser
# ────────────────────────────────────────────


def _extract_count(text: str) -> int:
    """Extract leading numeric count (e.g. '2Ps-…' → 2, '4□-…' → 4)."""
    text = re.sub(r"^[上下角内外]", "", text.strip())
    m = re.match(r"^(\d+)", text)
    return int(m.group(1)) if m else 1


def parse_member_entry(
    number: str,
    name: str,
    section_text: str,
    material: str,
    name_en: str = "",
) -> MemberEntry:
    """Parse a complete MEMBER LIST row."""
    # --- Lattice truss ---
    if "ラチス" in section_text:
        return _parse_lattice_entry(number, name, section_text, material, name_en)

    # --- Simple / compound sections ---
    parts = re.split(r"[,，]\s*", section_text)
    sections: list[SteelSection] = []
    total_w = 0.0

    for part in parts:
        part = part.strip()
        if not part:
            continue
        count = _extract_count(part)
        sec = parse_section(part)
        if sec:
            sections.append(sec)
            total_w += sec.unit_weight * count

    return MemberEntry(
        number=number,
        name=name,
        name_en=name_en,
        section_text=section_text,
        material=material,
        sections=sections,
        unit_weight=round(total_w, 3),
    )


def _parse_lattice_entry(
    number: str,
    name: str,
    section_text: str,
    material: str,
    name_en: str,
) -> MemberEntry:
    """Parse a lattice truss member entry.

    Supports multiple chord types (e.g. 外□-100×100 + 内□-60×60).
    """
    # Chord section(s): text before ラチス
    chord_text = section_text.split("ラチス")[0]

    # Lattice section: text after ラチス
    lattice_text = section_text.split("ラチス")[1]
    lattice = parse_section(lattice_text)

    # Parse all chord sections (may be multiple: 外□-..., 内□-...)
    chord_parts = re.split(r"[,，]\s*", chord_text)
    chords: list[tuple[int, SteelSection]] = []  # (count, section)
    for part in chord_parts:
        part = part.strip()
        if not part or re.match(r"^D[=＝]", part):
            continue
        count = _extract_count(part)
        sec = parse_section(part)
        if sec:
            chords.append((count, sec))

    # If only one chord type and count=1, default to 2 (top + bottom)
    if len(chords) == 1 and chords[0][0] == 1:
        chords[0] = (2, chords[0][1])

    total_chord_w = sum(c * s.unit_weight for c, s in chords)

    # Depth: D=450 or D=450,375 (tapered → average)
    depths: list[float] = []
    m_d = re.search(r"D[=＝](\d+\.?\d*)(?:[,，](\d+\.?\d*))?", section_text)
    if m_d:
        depths.append(float(m_d.group(1)))
        if m_d.group(2):
            depths.append(float(m_d.group(2)))
    depth = sum(depths) / len(depths) if depths else 0.0

    # Angle: θ=45°
    m_t = re.search(r"[θΘ][=＝](\d+\.?\d*)", section_text)
    angle = float(m_t.group(1)) if m_t else 45.0

    # Build sections list for display
    all_sections = [s for _, s in chords]
    if lattice:
        all_sections.append(lattice)

    # Primary chord for LatticeTrussSpec (use largest chord)
    primary_chord = max(chords, key=lambda cs: cs[1].area)[1] if chords else None
    primary_count = sum(c for c, _ in chords)

    if primary_chord and lattice and depth > 0:
        angle_rad = math.radians(angle)
        lattice_w = lattice.unit_weight / math.cos(angle_rad)
        total_w = total_chord_w + lattice_w

        truss = LatticeTrussSpec(
            chord=primary_chord,
            chord_count=primary_count,
            lattice=lattice,
            depth=depth,
            angle_deg=angle,
            chord_weight_per_m=round(total_chord_w, 3),
            lattice_weight_per_m=round(lattice_w, 3),
            total_weight_per_m=round(total_w, 3),
        )
        return MemberEntry(
            number=number,
            name=name,
            name_en=name_en,
            section_text=section_text,
            material=material,
            sections=all_sections,
            truss=truss,
            unit_weight=truss.total_weight_per_m,
        )

    # Fallback
    w = total_chord_w + (lattice.unit_weight if lattice else 0.0)
    return MemberEntry(
        number=number,
        name=name,
        name_en=name_en,
        section_text=section_text,
        material=material,
        sections=all_sections,
        unit_weight=round(w, 3),
    )


# ────────────────────────────────────────────
# FIX-R-15 member catalog (参考図4)
# ────────────────────────────────────────────


def build_fix_r15_catalog() -> MemberCatalog:
    """Build the complete MEMBER LIST for drawing FIX-R-15."""
    raw = [
        (
            "①",
            "主架構材",
            "main_frame",
            "2Ps-42.7φ×2.3t, D=450, ラチスP-42.7φ×1.9t, θ=45°",
            "STK400",
        ),
        ("②", "繋材", "tie_angle", "L-45×45×3t", "STK400"),
        (
            "③",
            "繋材",
            "tie_double_pipe",
            "上P-42.7φ×2.3t, 下P-42.7φ×2.3t",
            "STK400",
        ),
        ("④", "横継材", "purlin", "P-42.7φ×2.3t", "STK400"),
        ("⑤", "横継材", "purlin_large", "P-48.6φ×2.3t", "STK400"),
        ("⑤a", "横継材", "purlin_5a", "P-42.7φ×2.3t", "STK400"),
        ("⑤b", "横継材", "purlin_square", "□-50×50×2.3t", "STKR400"),
        ("⑥", "ブレース", "brace", "M12（T.B付き）", "SNR400B"),
        (
            "⑦",
            "水平梁材",
            "horizontal_tie",
            "外□-100×100×2.3t, 内□-60×60×2.3t, D=600, ラチスP-27.2φ×1.9t, θ=45°",
            "STKR400",
        ),
        ("⑧", "間柱材", "corner_column", "□-100×100×2.3t", "STKR400"),
        ("⑨", "束材", "post", "□-50×50×2.3t", "STKR400"),
        ("⑩", "胴繋材", "girt", "□-60×60×1.6t", "STKR400"),
        (
            "⑪",
            "側面開口梁材",
            "opening_beam",
            "4□-50×50×2.3t, D=450,375, ラチスP-34.0φ×2.3t, θ=45°",
            "STKR400",
        ),
        (
            "⑫",
            "側面開口柱材",
            "opening_column",
            "2□-125×75×2.3t, D=450, ラチスP-34.0φ×2.3t, θ=45°",
            "STKR400",
        ),
    ]

    entries = [
        parse_member_entry(num, name, sec, mat, name_en)
        for num, name, name_en, sec, mat in raw
    ]

    return MemberCatalog(drawing_number="FIX-R-15", entries=entries)


# ────────────────────────────────────────────
# Utility
# ────────────────────────────────────────────


def print_catalog(catalog: MemberCatalog) -> None:
    """Print formatted member catalog summary."""
    print(f"\n{'='*80}")
    print(f"  MEMBER LIST — {catalog.drawing_number}")
    print(f"{'='*80}")
    print(f"{'NO.':<5} {'名称':<10} {'断面':^30} {'材質':<10} {'kg/m':>8}")
    print(f"{'-'*80}")

    for e in catalog.entries:
        sec_display = e.section_text
        if len(sec_display) > 30:
            sec_display = sec_display[:27] + "..."
        print(
            f"{e.number:<5} {e.name:<10} {sec_display:<30} {e.material:<10} "
            f"{e.unit_weight:>8.3f}"
        )
        if e.truss:
            t = e.truss
            print(
                f"{'':>5} {'':>10}  ├ 弦材: {t.chord_count}×{t.chord.notation}"
                f" = {t.chord_weight_per_m:.3f} kg/m"
            )
            print(
                f"{'':>5} {'':>10}  └ ラチス: {t.lattice.notation}"
                f" / cos({t.angle_deg:.0f}°) = {t.lattice_weight_per_m:.3f} kg/m"
            )

    print(f"{'='*80}")


if __name__ == "__main__":
    catalog = build_fix_r15_catalog()
    print_catalog(catalog)
