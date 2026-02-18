"""Detect members from 軸組図 elevations via leader line tracing.

Supports five views: Y1通り, Y2通り, X1通り, Xn+1通り, X2~Xn通り.
"""

import re

import fitz

from .koyafuse import (
    _detect_members,
    _extract_visual_lines,
    _extract_visual_texts,
    _to_visual,
)
from .models import AxialFrameResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_scale(texts: list[tuple[str, float, float]]) -> str:
    """Extract scale string like '1/100' from text annotations."""
    for t, cx, cy in texts:
        m = re.search(r"S=1/(\d+)", t)
        if m and ("軸" in t or "組" in t or "断面" in t):
            return "1/" + m.group(1)
    for t, cx, cy in texts:
        m = re.search(r"S=1/(\d+)", t)
        if m:
            return "1/" + m.group(1)
    return ""


def _detect_for_bbox(
    doc: fitz.Document,
    page_index: int,
    drawing_bbox: dict | None,
) -> AxialFrameResult | None:
    """Run member detection on a specific page region."""
    if drawing_bbox is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mw = page.mediabox.width

    texts = _extract_visual_texts(page, rot, mw)
    lines = _extract_visual_lines(page, rot, mw)
    scale = _find_scale(texts)

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


# ---------------------------------------------------------------------------
# Page finders
# ---------------------------------------------------------------------------

def _find_axial_frame_page(doc: fitz.Document) -> int | None:
    """Find the first page containing 軸組図 (Y-direction views, page 3)."""
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


def _find_all_axial_pages(doc: fitz.Document) -> list[int]:
    """Find all page indices containing 軸組図 or 断面図."""
    pages = []
    for i in range(1, len(doc)):
        page = doc[i]
        text = page.get_text()
        hit = ("軸組図" in text or "軸　組　図" in text
               or "断面図" in text or "断　面　図" in text)
        if not hit:
            for annot in page.annots() or []:
                content = annot.info.get("content", "")
                if ("軸組図" in content or "軸　組　図" in content
                        or "断面図" in content or "断　面　図" in content):
                    hit = True
                    break
        if hit and i not in pages:
            pages.append(i)
    return pages


def _find_x_frame_page(doc: fitz.Document) -> int | None:
    """Find the page containing X-direction axial frame views (X1, Xn+1, X2~Xn).

    This is typically the page after the Y-direction page, containing
    Y1/Y2 as grid labels (not as view titles).
    """
    axial_pages = _find_all_axial_pages(doc)
    if len(axial_pages) >= 2:
        return axial_pages[1]
    # Fallback: look for X1通り or Xn+1通り in annotations
    for i in range(1, len(doc)):
        page = doc[i]
        for annot in page.annots() or []:
            content = annot.info.get("content", "")
            if "X1通" in content or "Xn" in content or "XN" in content:
                return i
    return None


# ---------------------------------------------------------------------------
# Bounding-box finders
# ---------------------------------------------------------------------------

def _find_y1_drawing_bbox(
    texts: list[tuple[str, float, float]],
    page_height: float,
) -> dict | None:
    """Y1 drawing: top half of the Y-axis page."""
    y1_title_y = None
    y2_title_y = None

    for t, cx, cy in texts:
        if "Y1通り" in t or "Y1通" in t:
            y1_title_y = cy
        if "Y2通り" in t or "Y2通" in t:
            y2_title_y = cy

    page_mid = page_height / 2
    x1_pos = None
    x_end_pos = None
    top_y = page_height

    for t, cx, cy in texts:
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
        if y1_title_y and y2_title_y:
            bottom = min(y1_title_y, y2_title_y)
            return {
                "x0": 30,
                "y0": 20,
                "x1": page_height * 0.7,
                "y1": bottom + 20,
            }
        return None

    x_min = min(x1_pos[0], x_end_pos[0])
    x_max = max(x1_pos[0], x_end_pos[0])
    bottom_y = y1_title_y + 30 if y1_title_y else page_mid

    return {
        "x0": x_min - 40,
        "y0": top_y - 40,
        "x1": x_max + 50,
        "y1": bottom_y,
    }


def _find_y2_drawing_bbox(
    texts: list[tuple[str, float, float]],
    page_height: float,
) -> dict | None:
    """Y2 drawing: bottom half of the Y-axis page."""
    y2_title_y = None

    for t, cx, cy in texts:
        if "Y2通り" in t or "Y2通" in t:
            y2_title_y = cy

    page_mid = page_height / 2
    x1_pos = None
    x_end_pos = None
    top_y = page_height

    for t, cx, cy in texts:
        # Only look in bottom half
        if cy < page_mid - 50:
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
        if y2_title_y:
            return {
                "x0": 30,
                "y0": page_mid - 30,
                "x1": page_height * 0.7,
                "y1": y2_title_y + 30,
            }
        return None

    x_min = min(x1_pos[0], x_end_pos[0])
    x_max = max(x1_pos[0], x_end_pos[0])
    bottom_y = y2_title_y + 30 if y2_title_y else page_height - 40

    return {
        "x0": x_min - 40,
        "y0": top_y - 40,
        "x1": x_max + 50,
        "y1": bottom_y,
    }


def _find_x1_drawing_bbox(
    texts: list[tuple[str, float, float]],
    page_width: float,
    page_height: float,
) -> dict | None:
    """X1 drawing: top-left area of the X-axis page.

    Grid labels are Y2 (left) and Y1 (right).
    """
    title_y = None
    for t, cx, cy in texts:
        if "X1通" in t and cx < page_width / 2:
            title_y = cy

    page_mid_y = page_height / 2
    y2_pos = None
    y1_pos = None
    top_y = page_height

    for t, cx, cy in texts:
        if cy > page_mid_y + 50:
            continue
        if cx > page_width / 2:
            continue
        if t.strip() == "Y2":
            y2_pos = (cx, cy)
            if cy < top_y:
                top_y = cy
        elif t.strip() == "Y1":
            y1_pos = (cx, cy)
            if cy < top_y:
                top_y = cy

    if not y2_pos or not y1_pos:
        if title_y:
            return {
                "x0": 30,
                "y0": 20,
                "x1": page_width * 0.48,
                "y1": title_y + 30,
            }
        return None

    x_min = min(y2_pos[0], y1_pos[0])
    x_max = max(y2_pos[0], y1_pos[0])
    bottom_y = title_y + 30 if title_y else page_mid_y

    return {
        "x0": x_min - 40,
        "y0": top_y - 40,
        "x1": x_max + 50,
        "y1": bottom_y,
    }


def _find_xn1_drawing_bbox(
    texts: list[tuple[str, float, float]],
    page_width: float,
    page_height: float,
) -> dict | None:
    """Xn+1 drawing: bottom-left area of the X-axis page.

    Grid labels are Y1 (left) and Y2 (right).
    """
    title_y = None
    for t, cx, cy in texts:
        if ("Xn+1通" in t or "XN+1通" in t or "Xn 1通" in t) and cx < page_width / 2:
            title_y = cy
        # Also match "Xn" near "1通り" as separate annotations
        if "Xn" in t and cx < page_width / 2 and cy > page_height / 2 - 50:
            title_y = cy if title_y is None else title_y

    page_mid_y = page_height / 2
    y1_pos = None
    y2_pos = None
    top_y = page_height

    for t, cx, cy in texts:
        # Bottom half, left side
        if cy < page_mid_y - 50:
            continue
        if cx > page_width / 2:
            continue
        if t.strip() == "Y1":
            y1_pos = (cx, cy)
            if cy < top_y:
                top_y = cy
        elif t.strip() == "Y2":
            y2_pos = (cx, cy)
            if cy < top_y:
                top_y = cy

    if not y1_pos or not y2_pos:
        if title_y:
            return {
                "x0": 30,
                "y0": page_mid_y - 30,
                "x1": page_width * 0.48,
                "y1": title_y + 30,
            }
        return None

    x_min = min(y1_pos[0], y2_pos[0])
    x_max = max(y1_pos[0], y2_pos[0])
    bottom_y = title_y + 30 if title_y else page_height - 40

    return {
        "x0": x_min - 40,
        "y0": top_y - 40,
        "x1": x_max + 50,
        "y1": bottom_y,
    }


def _find_x2xn_drawing_bbox(
    texts: list[tuple[str, float, float]],
    page_width: float,
    page_height: float,
) -> dict | None:
    """X2~Xn drawing: bottom-right area of the X-axis page (断面図).

    Grid labels are Y2 (left) and Y1 (right).
    """
    title_y = None
    for t, cx, cy in texts:
        if ("X2" in t and "Xn" in t) or ("X2~Xn" in t) or ("X2～Xn" in t):
            if cx > page_width / 2:
                title_y = cy
        if "断面図" in t and cx > page_width / 2:
            title_y = cy if title_y is None else title_y

    page_mid_y = page_height / 2
    y2_pos = None
    y1_pos = None
    top_y = page_height

    for t, cx, cy in texts:
        # Bottom half, right side
        if cy < page_mid_y - 50:
            continue
        if cx < page_width / 2:
            continue
        if t.strip() == "Y2":
            y2_pos = (cx, cy)
            if cy < top_y:
                top_y = cy
        elif t.strip() == "Y1":
            y1_pos = (cx, cy)
            if cy < top_y:
                top_y = cy

    if not y2_pos or not y1_pos:
        if title_y:
            return {
                "x0": page_width * 0.52,
                "y0": page_mid_y - 30,
                "x1": page_width - 30,
                "y1": title_y + 30,
            }
        return None

    x_min = min(y2_pos[0], y1_pos[0])
    x_max = max(y2_pos[0], y1_pos[0])
    bottom_y = title_y + 30 if title_y else page_height - 40

    return {
        "x0": x_min - 40,
        "y0": top_y - 40,
        "x1": x_max + 50,
        "y1": bottom_y,
    }


# ---------------------------------------------------------------------------
# Public detection entry points
# ---------------------------------------------------------------------------

def detect_axial_frame_members(doc: fitz.Document) -> AxialFrameResult | None:
    """Detect members from 軸組図 Y1通り."""
    if len(doc) < 3:
        return None

    page_index = _find_axial_frame_page(doc)
    if page_index is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mw = page.mediabox.width
    texts = _extract_visual_texts(page, rot, mw)
    drawing_bbox = _find_y1_drawing_bbox(texts, page.rect.height)

    return _detect_for_bbox(doc, page_index, drawing_bbox)


def detect_axial_frame_y2(doc: fitz.Document) -> AxialFrameResult | None:
    """Detect members from 軸組図 Y2通り."""
    if len(doc) < 3:
        return None

    page_index = _find_axial_frame_page(doc)
    if page_index is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mw = page.mediabox.width
    texts = _extract_visual_texts(page, rot, mw)
    drawing_bbox = _find_y2_drawing_bbox(texts, page.rect.height)

    return _detect_for_bbox(doc, page_index, drawing_bbox)


def detect_axial_frame_x1(doc: fitz.Document) -> AxialFrameResult | None:
    """Detect members from 軸組図 X1通り."""
    if len(doc) < 3:
        return None

    page_index = _find_x_frame_page(doc)
    if page_index is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mw = page.mediabox.width
    texts = _extract_visual_texts(page, rot, mw)
    drawing_bbox = _find_x1_drawing_bbox(texts, page.rect.width, page.rect.height)

    return _detect_for_bbox(doc, page_index, drawing_bbox)


def detect_axial_frame_xn1(doc: fitz.Document) -> AxialFrameResult | None:
    """Detect members from 軸組図 Xn+1通り."""
    if len(doc) < 3:
        return None

    page_index = _find_x_frame_page(doc)
    if page_index is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mw = page.mediabox.width
    texts = _extract_visual_texts(page, rot, mw)
    drawing_bbox = _find_xn1_drawing_bbox(texts, page.rect.width, page.rect.height)

    return _detect_for_bbox(doc, page_index, drawing_bbox)


def detect_axial_frame_x2xn(doc: fitz.Document) -> AxialFrameResult | None:
    """Detect members from 断面図 X2~Xn通り."""
    if len(doc) < 3:
        return None

    page_index = _find_x_frame_page(doc)
    if page_index is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mw = page.mediabox.width
    texts = _extract_visual_texts(page, rot, mw)
    drawing_bbox = _find_x2xn_drawing_bbox(texts, page.rect.width, page.rect.height)

    return _detect_for_bbox(doc, page_index, drawing_bbox)
