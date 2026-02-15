"""Step B: Detect views (屋根伏図, 平面図, 立面図, 断面図) and segment the page.

Key convention for Japanese engineering drawings:
  The view title label (e.g. "屋根伏図 S=1/150") is placed at the BOTTOM
  of its view area.  The drawing content extends UPWARD from the label.
"""

import re

from .models import BBox, Line, PagePrimitives, TextSpan, View, ViewType
from .primitives import lines_in_bbox, texts_in_bbox

# Handle full-width spaces between kanji
VIEW_PATTERNS: dict[ViewType, re.Pattern] = {
    ViewType.ROOF_PLAN: re.compile(r"屋[\s\u3000]*根[\s\u3000]*伏[\s\u3000]*図"),
    ViewType.FLOOR_PLAN: re.compile(r"平[\s\u3000]*面[\s\u3000]*図"),
    ViewType.ELEVATION: re.compile(r"立[\s\u3000]*面[\s\u3000]*図"),
    ViewType.SECTION: re.compile(r"断[\s\u3000]*面[\s\u3000]*図"),
}

SCALE_PATTERN = re.compile(r"S\s*[=＝]\s*1\s*/\s*(\d+)")

# Subtitle for elevations: 〈Y1通り〉, （Xn+1通り）, etc.
SUBTITLE_PATTERN = re.compile(r"[（(〈<]?\s*([XYxy]\S*通り)\s*[）)〉>]?")


# ---------------------------------------------------------------------------
# Coordinate transforms  (mediabox ↔ visual)
# ---------------------------------------------------------------------------

def _to_visual(mx: float, my: float, rot: int, mw: float, mh: float) -> tuple[float, float]:
    """Transform a mediabox point to visual (rendered) coordinates."""
    if rot == 270:
        return my, mw - mx
    elif rot == 90:
        return mh - my, mx
    elif rot == 180:
        return mw - mx, mh - my
    return mx, my


def _to_mediabox(vx: float, vy: float, rot: int, mw: float, mh: float) -> tuple[float, float]:
    """Transform a visual point back to mediabox coordinates."""
    if rot == 270:
        return mw - vy, vx
    elif rot == 90:
        return vy, mh - vx
    elif rot == 180:
        return mw - vx, mh - vy
    return vx, vy


def _vis_rect_to_mediabox(
    vx0: float, vy0: float, vx1: float, vy1: float,
    rot: int, mw: float, mh: float,
) -> BBox:
    """Convert a visual-space rectangle to a mediabox BBox."""
    corners = [
        _to_mediabox(vx0, vy0, rot, mw, mh),
        _to_mediabox(vx1, vy0, rot, mw, mh),
        _to_mediabox(vx0, vy1, rot, mw, mh),
        _to_mediabox(vx1, vy1, rot, mw, mh),
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def segment_views(
    primitives: PagePrimitives,
    page_rotation: int = 0,
) -> list[View]:
    """Detect view titles and segment page into view regions."""
    mw = primitives.page_width
    mh = primitives.page_height
    rot = page_rotation
    page_bbox = BBox(x0=0, y0=0, x1=mw, y1=mh)

    # Visual page dimensions
    if rot in (90, 270):
        vis_w, vis_h = mh, mw
    else:
        vis_w, vis_h = mw, mh

    # B1: Find ALL view titles (multiple elevations, etc.)
    title_matches = _find_all_view_titles(primitives.texts)

    if not title_matches:
        return [View(
            view_type=ViewType.UNKNOWN,
            title_text="(entire page)",
            title_bbox=page_bbox,
            region=page_bbox,
            texts=primitives.texts,
            lines=primitives.lines,
        )]

    # Convert title positions to visual coordinates
    vis_titles: list[tuple[ViewType, TextSpan, str | None, float, float]] = []
    for vtype, title_span, scale in title_matches:
        tc = title_span.center
        vx, vy = _to_visual(tc.x, tc.y, rot, mw, mh)
        vis_titles.append((vtype, title_span, scale, vx, vy))

    # Detect info panels (spec tables) to exclude from view regions
    info_panels = _detect_info_panels(primitives.lines, rot, mw, mh, vis_w)

    # B2: Build regions in visual space (label = bottom of view)
    vis_regions = _build_visual_regions(vis_titles, vis_w, vis_h)

    # Clip regions against info panels
    vis_regions = _clip_regions_against_panels(vis_regions, vis_titles, info_panels)

    # B3: Convert back to mediabox and assemble View objects
    views: list[View] = []
    for (vtype, title_span, scale, _vx, _vy), (rvx0, rvy0, rvx1, rvy1) in zip(
        vis_titles, vis_regions
    ):
        mb_region = _vis_rect_to_mediabox(rvx0, rvy0, rvx1, rvy1, rot, mw, mh)
        # Clip to page
        mb_region = BBox(
            x0=max(mb_region.x0, 0),
            y0=max(mb_region.y0, 0),
            x1=min(mb_region.x1, mw),
            y1=min(mb_region.y1, mh),
        )
        view_texts = texts_in_bbox(primitives.texts, mb_region)
        view_lines = lines_in_bbox(primitives.lines, mb_region)

        # Try to find subtitle for elevation views (e.g. "Y1通り")
        subtitle = _find_subtitle(title_span, primitives.texts)
        title_text = title_span.text
        if subtitle:
            title_text += f" {subtitle}"

        views.append(View(
            view_type=vtype,
            title_text=title_text,
            title_bbox=title_span.bbox,
            region=mb_region,
            scale=scale,
            texts=view_texts,
            lines=view_lines,
        ))

    return views


# ---------------------------------------------------------------------------
# Title detection
# ---------------------------------------------------------------------------

def _find_all_view_titles(
    texts: list[TextSpan],
) -> list[tuple[ViewType, TextSpan, str | None]]:
    """Find ALL view title labels, allowing multiple of the same type.

    Filters out combined title-block labels like "屋根伏図・平面図・立面図・断面図".
    """
    matches: list[tuple[ViewType, TextSpan, str | None]] = []

    for text_span in texts:
        for vtype, pattern in VIEW_PATTERNS.items():
            if not pattern.search(text_span.text):
                continue

            scale_match = SCALE_PATTERN.search(text_span.text)
            scale = f"1/{scale_match.group(1)}" if scale_match else None

            # Score: higher = more likely a real standalone title
            score = 0
            if scale:
                score += 10
            if len(text_span.text) <= 30:
                score += 5
            # Penalise if text contains multiple view-type names (title block)
            match_count = sum(
                1 for p in VIEW_PATTERNS.values() if p.search(text_span.text)
            )
            if match_count > 1:
                score -= 20

            if score > 0:
                matches.append((vtype, text_span, scale))
            break  # each text span matches at most one pattern

    # If scale not in title text, search nearby texts
    for i, (vtype, title_span, scale) in enumerate(matches):
        if scale is not None:
            continue
        for text_span in texts:
            if text_span is title_span:
                continue
            dx = abs(text_span.center.x - title_span.center.x)
            dy = abs(text_span.center.y - title_span.center.y)
            if dx < 200 and dy < 50:
                sm = SCALE_PATTERN.search(text_span.text)
                if sm:
                    matches[i] = (vtype, title_span, f"1/{sm.group(1)}")
                    break

    return matches


# ---------------------------------------------------------------------------
# Region building (visual space)
# ---------------------------------------------------------------------------

_ROW_THRESHOLD = 60.0   # pts – titles with vis_y within this are same row
_LABEL_PAD = 30.0       # pts – padding below label to include the label box
_INFO_PANEL_MARGIN = 20.0  # pts – gap between drawing area and info panel


def _detect_info_panels(
    lines: list[Line],
    rot: int, mw: float, mh: float,
    vis_w: float,
) -> list[tuple[float, float, float, float]]:
    """Detect specification/info table regions in visual space.

    Looks for clusters of horizontal lines forming a table in the right
    portion of the page.  Returns list of (vx0, vy0, vx1, vy1).
    """
    # Transform horizontal lines (>100pt) to visual space
    h_lines_vis: list[tuple[float, float, float]] = []  # (x0, x1, y)
    for l in lines:
        vp1 = _to_visual(l.p1.x, l.p1.y, rot, mw, mh)
        vp2 = _to_visual(l.p2.x, l.p2.y, rot, mw, mh)
        dx = abs(vp1[0] - vp2[0])
        dy = abs(vp1[1] - vp2[1])
        if dy < 2 and dx > 100:
            vx_min = min(vp1[0], vp2[0])
            vx_max = max(vp1[0], vp2[0])
            vy = (vp1[1] + vp2[1]) / 2
            # Only consider lines starting in the right 40% of the page
            if vx_min > vis_w * 0.6:
                h_lines_vis.append((vx_min, vx_max, vy))

    if len(h_lines_vis) < 5:
        return []

    # Cluster lines with similar x-extent (x0 and x1 within tolerance)
    xtol = 50
    used: set[int] = set()
    panels: list[tuple[float, float, float, float]] = []

    for i, (x0i, x1i, _yi) in enumerate(h_lines_vis):
        if i in used:
            continue
        cluster = [i]
        used.add(i)
        for j, (x0j, x1j, _yj) in enumerate(h_lines_vis):
            if j in used:
                continue
            if abs(x0j - x0i) < xtol and abs(x1j - x1i) < xtol:
                cluster.append(j)
                used.add(j)
        if len(cluster) >= 5:
            cx0 = min(h_lines_vis[k][0] for k in cluster)
            cx1 = max(h_lines_vis[k][1] for k in cluster)
            cy0 = min(h_lines_vis[k][2] for k in cluster)
            cy1 = max(h_lines_vis[k][2] for k in cluster)
            panels.append((cx0, cy0, cx1, cy1))

    return panels


def _clip_regions_against_panels(
    regions: list[tuple[float, float, float, float]],
    vis_titles: list[tuple[ViewType, TextSpan, str | None, float, float]],
    panels: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Clip view regions to exclude info panel areas.

    Only clips a region if the panel is to the right of the view title
    and the region vertically overlaps with the panel.
    """
    if not panels:
        return regions

    clipped = []
    for i, (rx0, ry0, rx1, ry1) in enumerate(regions):
        title_vx = vis_titles[i][3]
        for px0, py0, px1, py1 in panels:
            # Panel must be to the right of the title center
            if px0 <= title_vx:
                continue
            # Region must vertically overlap with panel
            if ry0 >= py1 or ry1 <= py0:
                continue
            # Clip right edge
            new_rx1 = px0 - _INFO_PANEL_MARGIN
            if new_rx1 > title_vx:  # Don't clip past the title
                rx1 = min(rx1, new_rx1)
        clipped.append((rx0, ry0, rx1, ry1))

    return clipped


def _build_visual_regions(
    vis_titles: list[tuple[ViewType, TextSpan, str | None, float, float]],
    vis_w: float,
    vis_h: float,
) -> list[tuple[float, float, float, float]]:
    """Build view regions in visual (landscape) space.

    Rule: the title label is at the BOTTOM of its view.
    """
    n = len(vis_titles)
    if n == 0:
        return []

    # --- Cluster into rows by vis_y proximity ---
    indices_by_y = sorted(range(n), key=lambda i: vis_titles[i][4])

    rows: list[list[int]] = []  # each row = list of indices
    current_row = [indices_by_y[0]]
    current_y = vis_titles[indices_by_y[0]][4]

    for idx in indices_by_y[1:]:
        vy = vis_titles[idx][4]
        if abs(vy - current_y) < _ROW_THRESHOLD:
            current_row.append(idx)
        else:
            rows.append(current_row)
            current_row = [idx]
            current_y = vy
    rows.append(current_row)

    # Sort each row left-to-right by vis_x
    for row in rows:
        row.sort(key=lambda i: vis_titles[i][3])

    # --- Row boundaries ---
    # Label is at the bottom → row_bottom = label_y + padding
    row_bottoms: list[float] = []
    for row in rows:
        max_vy = max(vis_titles[i][4] for i in row)
        row_bottoms.append(max_vy + _LABEL_PAD)

    # row_top = previous row's bottom (first row starts at page top)
    row_tops: list[float] = [0.0]
    for i in range(1, len(rows)):
        row_tops.append(row_bottoms[i - 1])

    # Clamp last row bottom to page height
    row_bottoms[-1] = min(row_bottoms[-1], vis_h)

    # --- Column boundaries within each row ---
    regions: list[tuple[float, float, float, float] | None] = [None] * n

    for row_idx, row in enumerate(rows):
        ry0 = row_tops[row_idx]
        ry1 = row_bottoms[row_idx]

        if len(row) == 1:
            regions[row[0]] = (0.0, ry0, vis_w, ry1)
        else:
            vxs = [vis_titles[i][3] for i in row]
            col_edges = [0.0]
            for j in range(1, len(row)):
                col_edges.append((vxs[j - 1] + vxs[j]) / 2)
            col_edges.append(vis_w)

            for j, idx in enumerate(row):
                regions[idx] = (col_edges[j], ry0, col_edges[j + 1], ry1)

    # Safety: any None → full page
    for i in range(n):
        if regions[i] is None:
            regions[i] = (0.0, 0.0, vis_w, vis_h)

    return regions  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Subtitle detection (e.g. "〈Y1通り〉")
# ---------------------------------------------------------------------------

def _find_subtitle(title_span: TextSpan, all_texts: list[TextSpan]) -> str | None:
    """Look for a subtitle annotation near the view title."""
    tc = title_span.center
    for t in all_texts:
        if t is title_span:
            continue
        dx = abs(t.center.x - tc.x)
        dy = abs(t.center.y - tc.y)
        if dx < 250 and dy < 40:
            m = SUBTITLE_PATTERN.search(t.text)
            if m:
                return f"({m.group(1)})"
            if "通り" in t.text and len(t.text) < 20:
                return f"({t.text.strip()})"
    return None
