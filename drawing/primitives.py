"""Step A: Parse PDF with PyMuPDF and extract raw primitives + geometry helpers."""

import math

import fitz

from .models import BBox, Line, PagePrimitives, Point, TextSpan


def extract_page_primitives(page: fitz.Page) -> PagePrimitives:
    """Extract all texts, lines, and rectangles from a single PDF page."""
    texts = _extract_texts(page)
    # Also extract AutoCAD SHX text stored as annotations
    shx_texts = _extract_shx_annotations(page)
    texts.extend(shx_texts)
    lines, rects = _extract_lines_and_rects(page)
    # Use mediabox dimensions — drawings and annotations both use unrotated coords
    mb = page.mediabox
    return PagePrimitives(
        page_index=page.number,
        page_width=mb.width,
        page_height=mb.height,
        texts=texts,
        lines=lines,
        rects=rects,
    )


def _extract_texts(page: fitz.Page) -> list[TextSpan]:
    """Extract text spans from page.get_text('dict')."""
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    spans: list[TextSpan] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # type 0 = text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                bbox_raw = span["bbox"]  # (x0, y0, x1, y1)
                bbox = BBox(
                    x0=bbox_raw[0], y0=bbox_raw[1],
                    x1=bbox_raw[2], y1=bbox_raw[3],
                )
                spans.append(TextSpan(
                    text=text,
                    bbox=bbox,
                    center=bbox.center,
                    font=span.get("font", ""),
                    size=span.get("size", 0.0),
                ))
    return spans


def _extract_shx_annotations(page: fitz.Page) -> list[TextSpan]:
    """Extract AutoCAD SHX text stored as PDF annotations.

    AutoCAD exports SHX font text as Square-type annotations with:
    - info['title'] == 'AutoCAD SHX Text'
    - info['content'] == the actual text string
    - annot.rect == bounding box
    """
    spans: list[TextSpan] = []
    annots = page.annots()
    if not annots:
        return spans

    for annot in annots:
        info = annot.info
        content = info.get("content", "").strip()
        if not content:
            continue

        rect = annot.rect
        bbox = BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)
        spans.append(TextSpan(
            text=content,
            bbox=bbox,
            center=bbox.center,
            font="AutoCAD SHX",
            size=0.0,
        ))

    return spans


def _extract_lines_and_rects(page: fitz.Page) -> tuple[list[Line], list[BBox]]:
    """Extract vector lines and rectangles from page.get_drawings()."""
    lines: list[Line] = []
    rects: list[BBox] = []
    for path in page.get_drawings():
        stroke_color = path.get("color")
        stroke_width = path.get("width", 1.0)
        for item in path["items"]:
            kind = item[0]
            if kind == "l":  # line segment
                p1_raw, p2_raw = item[1], item[2]
                p1 = Point(x=p1_raw.x, y=p1_raw.y)
                p2 = Point(x=p2_raw.x, y=p2_raw.y)
                length = dist(p1, p2)
                if length < 0.5:  # skip degenerate lines
                    continue
                angle = math.degrees(math.atan2(p2.y - p1.y, p2.x - p1.x)) % 360
                lines.append(Line(
                    p1=p1, p2=p2,
                    length=length,
                    angle=angle,
                    width=stroke_width or 1.0,
                    color=list(stroke_color) if stroke_color else None,
                ))
            elif kind == "re":  # rectangle
                rect = item[1]
                rects.append(BBox(
                    x0=rect.x0, y0=rect.y0,
                    x1=rect.x1, y1=rect.y1,
                ))
    return lines, rects


# --- Geometry Helpers ---


def dist(a: Point, b: Point) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def is_horizontal(line: Line, tolerance_deg: float = 5.0) -> bool:
    a = line.angle % 180
    return a < tolerance_deg or a > (180 - tolerance_deg)


def is_vertical(line: Line, tolerance_deg: float = 5.0) -> bool:
    a = line.angle % 180
    return abs(a - 90) < tolerance_deg


def texts_in_bbox(texts: list[TextSpan], bbox: BBox) -> list[TextSpan]:
    return [t for t in texts if bbox.contains(t.center)]


def lines_in_bbox(lines: list[Line], bbox: BBox) -> list[Line]:
    return [
        ln for ln in lines
        if bbox.contains(ln.p1) or bbox.contains(ln.p2)
    ]


def nearby_texts(point: Point, texts: list[TextSpan], radius: float) -> list[TextSpan]:
    return [t for t in texts if dist(point, t.center) <= radius]


def nearby_lines(point: Point, lines: list[Line], radius: float) -> list[Line]:
    return [
        ln for ln in lines
        if dist(point, ln.p1) <= radius or dist(point, ln.p2) <= radius
    ]


def line_midpoint(line: Line) -> Point:
    return Point(x=(line.p1.x + line.p2.x) / 2, y=(line.p1.y + line.p2.y) / 2)


def point_to_line_distance(point: Point, line: Line) -> float:
    """Perpendicular distance from point to the infinite line through line segment."""
    dx = line.p2.x - line.p1.x
    dy = line.p2.y - line.p1.y
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-10:
        return dist(point, line.p1)
    t = ((point.x - line.p1.x) * dx + (point.y - line.p1.y) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    proj = Point(x=line.p1.x + t * dx, y=line.p1.y + t * dy)
    return dist(point, proj)
