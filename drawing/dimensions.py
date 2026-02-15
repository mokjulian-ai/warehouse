"""Step D: Extract dimensions (numeric values + measurement geometry)."""

import re

from .models import Dimension, DimensionType, Line, TextSpan, View
from .primitives import nearby_lines

# Dimension text patterns
PLAIN_DIM_PATTERN = re.compile(r"^(\d{2,6})$")
PITCH_PATTERN = re.compile(r"^[@＠][\s\u3000]*(\d{2,6})$")
REPEAT_PATTERN = re.compile(
    r"^(\d{2,6})[\s\u3000]*[×xX\uff58][\s\u3000]*(\d{1,3})$"
)
# Also match "n" as a symbolic count: "2000×n"
REPEAT_SYMBOLIC_PATTERN = re.compile(
    r"^(\d{2,6})[\s\u3000]*[×xX\uff58][\s\u3000]*([nNｎＮ])$"
)

DIM_LINE_SEARCH_RADIUS = 40.0


def extract_dimensions(views: list[View]) -> list[Dimension]:
    """Extract dimensions from all views."""
    all_dims: list[Dimension] = []
    for view in views:
        dims = _extract_from_view(view)
        for d in dims:
            d.source_view = view.view_type
        all_dims.extend(dims)
    return all_dims


def _extract_from_view(view: View) -> list[Dimension]:
    """Extract dimensions from a single view."""
    dims: list[Dimension] = []
    for text_span in view.texts:
        result = _match_dimension_text(text_span)
        if result is None:
            continue
        dim_type, value, count = result
        dim_lines = _find_dimension_lines(text_span, view.lines)
        dims.append(Dimension(
            value=value,
            raw_text=text_span.text,
            dim_type=dim_type,
            repeat_count=count,
            text_span=text_span,
            nearest_lines=dim_lines,
        ))
    return dims


def _match_dimension_text(
    text_span: TextSpan,
) -> tuple[DimensionType, float, int | None] | None:
    """Match a text span against dimension patterns."""
    text = text_span.text.strip()

    # Try pitch first (@2000)
    m = PITCH_PATTERN.match(text)
    if m:
        return DimensionType.PITCH, float(m.group(1)), None

    # Try repeat with numeric count (2000×3)
    m = REPEAT_PATTERN.match(text)
    if m:
        return DimensionType.REPEAT, float(m.group(1)), int(m.group(2))

    # Try repeat with symbolic count (2000×n)
    m = REPEAT_SYMBOLIC_PATTERN.match(text)
    if m:
        return DimensionType.REPEAT, float(m.group(1)), None

    # Try plain number (7500)
    m = PLAIN_DIM_PATTERN.match(text)
    if m:
        val = float(m.group(1))
        # Filter out numbers that are likely not dimensions
        # (too small for mm measurements in engineering drawings)
        if val < 10:
            return None
        return DimensionType.SINGLE, val, None

    return None


def _find_dimension_lines(
    text_span: TextSpan,
    lines: list[Line],
    radius: float = DIM_LINE_SEARCH_RADIUS,
) -> list[Line]:
    """Find dimension lines near a dimension text."""
    return nearby_lines(text_span.center, lines, radius)
