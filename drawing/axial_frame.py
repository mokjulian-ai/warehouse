"""Detect members from 軸組図 Y1通り (axial frame elevation) via leader line tracing."""

import re

import fitz

from .koyafuse import (
    _detect_members,
    _extract_visual_lines,
    _extract_visual_texts,
    _to_visual,
)
from .models import AxialFrameResult


def detect_axial_frame_members(doc: fitz.Document) -> AxialFrameResult | None:
    """Detect member labels and leader lines from the 軸組図 Y1通り.

    Scans pages for 軸組図, then isolates the Y1 drawing area.
    """
    if len(doc) < 3:
        return None

    page_index = _find_axial_frame_page(doc)
    if page_index is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mb = page.mediabox
    mw = mb.width

    texts = _extract_visual_texts(page, rot, mw)

    # Find scale
    scale = ""
    for t, cx, cy in texts:
        m = re.search(r"S=1/(\d+)", t)
        if m and ("軸" in t or "組" in t):
            scale = "1/" + m.group(1)
            break
    if not scale:
        for t, cx, cy in texts:
            m = re.search(r"S=1/(\d+)", t)
            if m:
                scale = "1/" + m.group(1)
                break

    # Find Y1 drawing area
    drawing_bbox = _find_y1_drawing_bbox(texts, page.rect.height)

    lines = _extract_visual_lines(page, rot, mw)

    detected = _detect_members(texts, lines, drawing_bbox)

    return AxialFrameResult(
        page_index=page_index,
        scale=scale,
        detected_members=detected,
        drawing_bbox=drawing_bbox,
        mediabox_width=mw,
        page_visual_width=page.rect.width,
        page_visual_height=page.rect.height,
    )


def _find_axial_frame_page(doc: fitz.Document) -> int | None:
    """Find the page index containing 軸組図."""
    for i in range(1, len(doc)):
        page = doc[i]
        text = page.get_text()
        if "軸組図" in text or "軸　組　図" in text:
            return i
        for annot in page.annots() or []:
            content = annot.info.get("content", "")
            if "軸組図" in content or "軸　組　図" in content:
                return i
    return None


def _find_y1_drawing_bbox(
    texts: list[tuple[str, float, float]],
    page_height: float,
) -> dict | None:
    """Find the Y1 drawing area bounding box.

    Strategy: The Y1 drawing is in the top half of the page.
    Find "Y1通り" title text near the bottom of the Y1 drawing,
    and grid labels (X1, Xn+1) for X boundaries.
    """
    y1_title_y = None
    y2_title_y = None

    # Find Y1通り and Y2通り title positions
    for t, cx, cy in texts:
        if "Y1通り" in t or "Y1通" in t:
            y1_title_y = cy
        if "Y2通り" in t or "Y2通" in t:
            y2_title_y = cy

    # Find grid labels for X boundaries (in top half of page)
    page_mid = page_height / 2
    x1_pos = None
    x_end_pos = None
    top_y = page_height  # track topmost grid label

    for t, cx, cy in texts:
        # Only look in top half (Y1 drawing area)
        if cy > page_mid + 50:
            continue
        if t.strip() == "X1":
            x1_pos = (cx, cy)
            if cy < top_y:
                top_y = cy
        elif t.strip() in ("Xn+1", "XN+1"):
            x_end_pos = (cx, cy)
            if cy < top_y:
                top_y = cy

    if not x1_pos or not x_end_pos:
        # Fallback: use page boundaries for top half
        if y1_title_y and y2_title_y:
            # Y1 is above Y2
            bottom = min(y1_title_y, y2_title_y)
            return {
                "x0": 30,
                "y0": 20,
                "x1": page_height * 0.7,  # approximate
                "y1": bottom + 20,
            }
        return None

    x_min = min(x1_pos[0], x_end_pos[0])
    x_max = max(x1_pos[0], x_end_pos[0])

    # Bottom boundary: Y1通り title or midpoint
    bottom_y = y1_title_y + 30 if y1_title_y else page_mid

    return {
        "x0": x_min - 40,
        "y0": top_y - 40,
        "x1": x_max + 50,
        "y1": bottom_y,
    }
