"""Step E: Extract height parameters from engineering drawings."""

import re

from .models import HeightParam, HeightType, TextSpan, View, ViewType

# Patterns
EAVE_HEIGHT_PATTERN = re.compile(
    r"軒[\s\u3000]*高[\s\u3000]*[=＝:：]?[\s\u3000]*(\d{2,6})"
)
MAX_HEIGHT_PATTERN = re.compile(
    r"(\d{2,6})[\s\u3000]*[（(][\s\u3000]*建[\s\u3000]*築[\s\u3000]*物[\s\u3000]*の?"
    r"[\s\u3000]*最[\s\u3000]*高[\s\u3000]*高[\s\u3000]*さ[\s\u3000]*[)）]"
)
MAX_HEIGHT_ALT_PATTERN = re.compile(
    r"最[\s\u3000]*高[\s\u3000]*高[\s\u3000]*さ[\s\u3000]*[=＝:：]?[\s\u3000]*(\d{2,6})"
)
GL_PATTERN = re.compile(r"(設[\s\u3000]*計[\s\u3000]*)?GL")
FL_PATTERN = re.compile(r"(\d*)[\s\u3000]*FL")


def extract_heights(views: list[View]) -> list[HeightParam]:
    """Extract heights, primarily from 立面図 and 断面図."""
    all_heights: list[HeightParam] = []
    seen_types: set[HeightType] = set()

    # Prioritize elevation and section views
    priority_views = sorted(
        views,
        key=lambda v: 0 if v.view_type in (ViewType.ELEVATION, ViewType.SECTION) else 1,
    )

    for view in priority_views:
        heights = _extract_from_view(view)
        for h in heights:
            # Avoid duplicates (keep first found, from priority view)
            if h.height_type in seen_types and h.height_type not in (
                HeightType.GL, HeightType.FL
            ):
                continue
            h.source_view = view.view_type
            all_heights.append(h)
            seen_types.add(h.height_type)

    return all_heights


def _extract_from_view(view: View) -> list[HeightParam]:
    """Extract heights from a single view."""
    heights: list[HeightParam] = []

    for text_span in view.texts:
        result = _match_height_text(text_span, view.texts)
        if result:
            heights.append(result)

    return heights


def _match_height_text(
    text_span: TextSpan,
    all_texts: list[TextSpan],
) -> HeightParam | None:
    """Match a text span against height patterns."""
    text = text_span.text

    # 軒高
    m = EAVE_HEIGHT_PATTERN.search(text)
    if m:
        return HeightParam(
            height_type=HeightType.EAVE_HEIGHT,
            value=float(m.group(1)),
            raw_text=text,
            text_span=text_span,
        )

    # 最高高さ (number before parenthetical)
    m = MAX_HEIGHT_PATTERN.search(text)
    if m:
        return HeightParam(
            height_type=HeightType.MAX_HEIGHT,
            value=float(m.group(1)),
            raw_text=text,
            text_span=text_span,
        )

    # 最高高さ (alt format)
    m = MAX_HEIGHT_ALT_PATTERN.search(text)
    if m:
        return HeightParam(
            height_type=HeightType.MAX_HEIGHT,
            value=float(m.group(1)),
            raw_text=text,
            text_span=text_span,
        )

    # 設計GL
    m = re.search(r"設[\s\u3000]*計[\s\u3000]*GL", text)
    if m:
        return HeightParam(
            height_type=HeightType.DESIGN_GL,
            value=None,
            raw_text=text,
            text_span=text_span,
        )

    # GL (but not 設計GL which was already matched)
    m = re.match(r"^GL$", text.strip())
    if m:
        return HeightParam(
            height_type=HeightType.GL,
            value=None,
            raw_text=text,
            text_span=text_span,
        )

    # FL
    m = FL_PATTERN.search(text)
    if m and "FL" in text:
        value = float(m.group(1)) if m.group(1) else None
        return HeightParam(
            height_type=HeightType.FL,
            value=value,
            raw_text=text,
            text_span=text_span,
        )

    return None
