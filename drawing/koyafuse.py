"""Detect members from 小屋伏図 (roof framing plan) page via leader line tracing."""

import math
import re

import fitz

from .models import DetectedMember, KoyafuseResult, LeaderTip


def detect_koyafuse_members(doc: fitz.Document) -> KoyafuseResult | None:
    """Detect member labels and leader lines from the 小屋伏図 page.

    Scans pages 1+ looking for the 小屋伏図.  Returns None if not found
    or if the PDF has only one page.
    """
    if len(doc) < 2:
        return None

    # Find page containing 小屋伏図 (usually page 2)
    page_index = _find_koyafuse_page(doc)
    if page_index is None:
        return None

    page = doc[page_index]
    rot = page.rotation
    mb = page.mediabox
    mw = mb.width

    # Extract texts in visual coordinates
    texts = _extract_visual_texts(page, rot, mw)

    # Find scale text — may be combined with title or standalone
    scale = ""
    for t, cx, cy in texts:
        m = re.search(r"S=1/(\d+)", t)
        if m:
            # Prefer the one near the 小屋伏図 title
            if "小屋" in t or "伏図" in t or "伏　図" in t:
                scale = "1/" + m.group(1)
                break
    if not scale:
        for t, cx, cy in texts:
            m = re.search(r"S=1/(\d+)", t)
            if m:
                scale = "1/" + m.group(1)
                break

    # Find drawing area bounding box from grid labels
    drawing_bbox = _find_drawing_bbox(texts)

    # Extract lines in visual coordinates
    lines = _extract_visual_lines(page, rot, mw)

    # Detect member labels in the drawing area
    detected = _detect_members(texts, lines, drawing_bbox)

    return KoyafuseResult(
        page_index=page_index,
        scale=scale,
        detected_members=detected,
        drawing_bbox=drawing_bbox,
        mediabox_width=mw,
        page_visual_width=page.rect.width,
        page_visual_height=page.rect.height,
    )


def _find_koyafuse_page(doc: fitz.Document) -> int | None:
    """Find the page index containing 小屋伏図."""
    for i in range(1, len(doc)):
        page = doc[i]
        # Check regular text
        text = page.get_text()
        if "小屋伏図" in text or "小　屋　伏　図" in text:
            return i
        # Check SHX annotations (AutoCAD stores text as annotations)
        for annot in page.annots() or []:
            content = annot.info.get("content", "")
            if "小屋伏図" in content or "小　屋　伏　図" in content:
                return i
    return None


def _to_visual(mx: float, my: float, rot: int, mw: float) -> tuple[float, float]:
    """Convert mediabox coords to visual coords based on page rotation."""
    if rot == 270:
        return my, mw - mx
    elif rot == 90:
        return mw - my, mx  # placeholder; may need adjustment
    elif rot == 180:
        return mw - mx, mw - my
    return mx, my


def _extract_visual_texts(
    page: fitz.Page, rot: int, mw: float,
) -> list[tuple[str, float, float]]:
    """Extract all texts with visual coordinates."""
    results: list[tuple[str, float, float]] = []

    # Regular text spans
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                bb = span["bbox"]
                vx0, vy0 = _to_visual(bb[0], bb[1], rot, mw)
                vx1, vy1 = _to_visual(bb[2], bb[3], rot, mw)
                cx = (min(vx0, vx1) + max(vx0, vx1)) / 2
                cy = (min(vy0, vy1) + max(vy0, vy1)) / 2
                results.append((text, cx, cy))

    # SHX annotations
    for annot in page.annots() or []:
        content = annot.info.get("content", "").strip()
        if not content:
            continue
        r = annot.rect
        vx0, vy0 = _to_visual(r.x0, r.y0, rot, mw)
        vx1, vy1 = _to_visual(r.x1, r.y1, rot, mw)
        cx = (min(vx0, vx1) + max(vx0, vx1)) / 2
        cy = (min(vy0, vy1) + max(vy0, vy1)) / 2
        results.append((content, cx, cy))

    return results


def _extract_visual_lines(
    page: fitz.Page, rot: int, mw: float,
) -> list[tuple[float, float, float, float, float, float]]:
    """Extract lines as (vx1, vy1, vx2, vy2, length, width) in visual coords."""
    results = []
    for path in page.get_drawings():
        sw = path.get("width", 1.0)
        for item in path["items"]:
            if item[0] != "l":
                continue
            p1r, p2r = item[1], item[2]
            vx1, vy1 = _to_visual(p1r.x, p1r.y, rot, mw)
            vx2, vy2 = _to_visual(p2r.x, p2r.y, rot, mw)
            length = math.hypot(vx2 - vx1, vy2 - vy1)
            if length < 0.5:
                continue
            results.append((vx1, vy1, vx2, vy2, length, sw))
    return results


def _find_drawing_bbox(
    texts: list[tuple[str, float, float]],
) -> dict | None:
    """Find the 小屋伏図 drawing area from grid labels X1, Xn+1, Y1, Y2."""
    x1_pos = y1_pos = x_end_pos = y2_pos = None

    for t, cx, cy in texts:
        if t == "X1":
            x1_pos = (cx, cy)
        elif t in ("Xn+1", "XN+1"):
            x_end_pos = (cx, cy)
        elif t == "Y1":
            y1_pos = (cx, cy)
        elif t == "Y2":
            y2_pos = (cx, cy)

    if not all([x1_pos, x_end_pos, y1_pos, y2_pos]):
        return None

    # Drawing area extends between grid labels
    x_min = min(x1_pos[0], x_end_pos[0])
    x_max = max(x1_pos[0], x_end_pos[0])
    y_min = min(y2_pos[1], y1_pos[1])  # Y2 is typically upper, Y1 lower
    y_max = max(y2_pos[1], y1_pos[1])

    return {
        "x0": x_min - 30,
        "y0": y_min - 30,
        "x1": x_max + 50,
        "y1": y_max + 30,
    }


def _detect_members(
    texts: list[tuple[str, float, float]],
    lines: list[tuple[float, float, float, float, float, float]],
    drawing_bbox: dict | None,
) -> list[DetectedMember]:
    """Detect member labels and trace their leader lines."""
    # 1. Find all single/double digit texts that could be member numbers
    member_pattern = re.compile(r"^[1-9]$|^1[0-2]$")
    modifier_pattern = re.compile(r"^(内側|外側)$")

    label_candidates: list[tuple[str, float, float]] = []
    modifier_texts: list[tuple[str, float, float]] = []

    for t, cx, cy in texts:
        if member_pattern.match(t):
            label_candidates.append((t, cx, cy))
        elif modifier_pattern.match(t):
            modifier_texts.append((t, cx, cy))

    # 2. Filter to drawing area only (exclude MEMBER LIST table)
    drawing_labels: list[tuple[str, str, float, float]] = []  # (number, modifier, x, y)

    for num, cx, cy in label_candidates:
        # Exclude labels in MEMBER LIST table area (far right) or header (top)
        if drawing_bbox:
            if cx < drawing_bbox["x0"] or cx > drawing_bbox["x1"]:
                continue
            if cy < drawing_bbox["y0"] or cy > drawing_bbox["y1"]:
                continue
        else:
            # Fallback: exclude obvious table areas
            if cx > 830 or cy < 50:
                continue

        # Check for nearby modifier
        modifier = ""
        for mt, mx, my in modifier_texts:
            if math.hypot(mx - cx, my - cy) < 40:
                modifier = mt
                break

        drawing_labels.append((num, modifier, cx, cy))

    # 3. For each label, find all leader line tips
    detected: list[DetectedMember] = []

    for num, modifier, cx, cy in drawing_labels:
        tips = _find_leader_tips(cx, cy, lines)
        label = num + modifier

        detected.append(DetectedMember(
            member_number=num,
            modifier=modifier,
            label=label,
            label_x=round(cx, 1),
            label_y=round(cy, 1),
            leader_tips=tips,
            tip_count=len(tips),
        ))

    # 4. Determine orientation by grouping all tips of same label
    label_tips: dict[str, list[LeaderTip]] = {}
    for m in detected:
        label_tips.setdefault(m.label, []).extend(m.leader_tips)

    label_orient: dict[str, str] = {}
    for label, all_tips in label_tips.items():
        label_orient[label] = _determine_orientation(all_tips, lines)

    for m in detected:
        m.orientation = label_orient.get(m.label, "")

    # 5. Count structural lines (including unlabeled ones)
    _count_structural_lines(detected, lines, drawing_bbox)

    # Sort by member number then modifier
    detected.sort(key=lambda m: (int(m.member_number), m.modifier))
    return detected


def _count_structural_lines(
    detected: list[DetectedMember],
    lines: list[tuple[float, float, float, float, float, float]],
    drawing_bbox: dict | None,
) -> None:
    """Count total structural lines for each member, including unlabeled ones.

    For X-direction members (purlins): line_count = tip_count (each tip = one line).
    For Y-direction members (frames): find all vertical structural lines with
    matching length in the drawing area and count distinct X positions.
    """
    if not drawing_bbox:
        for m in detected:
            m.line_count = m.tip_count
        return

    bx0 = drawing_bbox["x0"] - 5
    bx1 = drawing_bbox["x1"] + 5
    by0 = drawing_bbox["y0"] - 20
    by1 = drawing_bbox["y1"] + 20

    # Build a map of vertical structural lines grouped by length category
    # (x_position, y_center, line_length) for all long vertical lines in drawing area
    vert_lines: list[tuple[float, float, float]] = []  # (x_center, y_center, length)
    for lx1, ly1, lx2, ly2, llen, lw in lines:
        if abs(lw - 0.42) > 0.05 or llen < 150:
            continue
        angle = math.degrees(math.atan2(abs(ly2 - ly1), abs(lx2 - lx1)))
        if angle < 80:
            continue
        cx = (lx1 + lx2) / 2
        cy = (ly1 + ly2) / 2
        if bx0 <= cx <= bx1 and by0 <= cy <= by1:
            vert_lines.append((cx, cy, llen))

    # Group into distinct X positions (merge within 5pts)
    vert_lines.sort()
    vert_positions: list[tuple[float, float, float]] = []  # (x, y_center, max_length)
    for cx, cy, llen in vert_lines:
        if vert_positions and abs(cx - vert_positions[-1][0]) <= 5:
            prev_x, prev_cy, prev_len = vert_positions[-1]
            vert_positions[-1] = (prev_x, (prev_cy + cy) / 2, max(prev_len, llen))
        else:
            vert_positions.append((cx, cy, llen))

    for m in detected:
        if m.orientation == "x":
            # For purlins: each leader tip = one structural line
            m.line_count = m.tip_count
            m.line_positions = [[t.x, t.y] for t in m.leader_tips]
        elif m.orientation == "y" and m.leader_tips:
            # Find the structural line length at this member's tip
            tip = m.leader_tips[0]
            ref_len = 0.0
            best_dist = float("inf")
            for lx1, ly1, lx2, ly2, llen, lw in lines:
                if abs(lw - 0.42) > 0.05 or llen < 150:
                    continue
                angle = math.degrees(
                    math.atan2(abs(ly2 - ly1), abs(lx2 - lx1))
                )
                if angle < 80:
                    continue
                d = _point_to_segment_dist(tip.x, tip.y, lx1, ly1, lx2, ly2)
                if d < best_dist:
                    best_dist = d
                    ref_len = llen

            if ref_len > 0:
                # Collect vertical lines with similar length (within 5%)
                tol = ref_len * 0.05
                matched = [
                    (vx, vy) for vx, vy, vlen in vert_positions
                    if abs(vlen - ref_len) <= tol
                ]
                m.line_count = len(matched)
                m.line_positions = [[vx, vy] for vx, vy in matched]
            else:
                m.line_count = m.tip_count
                m.line_positions = [[t.x, t.y] for t in m.leader_tips]
        else:
            m.line_count = m.tip_count
            m.line_positions = [[t.x, t.y] for t in m.leader_tips]


def _find_leader_tips(
    label_x: float,
    label_y: float,
    lines: list[tuple[float, float, float, float, float, float]],
    near_radius: float = 15.0,
    min_length: float = 5.0,
) -> list[LeaderTip]:
    """Find all leader line arrow tips from a label.

    The leader structure is: label -> short stub -> junction -> N arrow branches.
    We find candidate junction points by following thin lines from the label,
    then pick the junction with the most outgoing thin lines.
    """
    snap = 3.0  # pts tolerance for endpoint matching

    # Step 1: find candidate junctions — collect far endpoints of thin lines
    # that have one endpoint near the label
    candidates: list[tuple[float, float, float]] = []  # (x, y, dist_from_label)

    for lx1, ly1, lx2, ly2, llen, lw in lines:
        if abs(lw - 0.30) > 0.05:
            continue
        if llen < 2:
            continue

        d1 = math.hypot(lx1 - label_x, ly1 - label_y)
        d2 = math.hypot(lx2 - label_x, ly2 - label_y)

        if d1 <= near_radius:
            candidates.append((lx2, ly2, d1))
        if d2 <= near_radius:
            candidates.append((lx1, ly1, d2))

    if not candidates:
        return []

    # Step 2: for each candidate junction, score it by the total length of
    # thin lines emanating from it.  This prefers real leader junctions
    # (with long branches) over dimension-tick clusters (many short ticks).
    def _score_junction(ox: float, oy: float) -> tuple[float, int]:
        max_len = 0.0
        count = 0
        for lx1, ly1, lx2, ly2, llen, lw in lines:
            if abs(lw - 0.30) > 0.05 or llen < min_length:
                continue
            d1 = math.hypot(lx1 - ox, ly1 - oy)
            d2 = math.hypot(lx2 - ox, ly2 - oy)
            if d1 <= snap or d2 <= snap:
                count += 1
                if llen > max_len:
                    max_len = llen
        return (max_len, count)

    # De-dup candidate points by rounding
    seen_cands: dict[tuple[int, int], tuple[float, float]] = {}
    for cx, cy, _ in candidates:
        key = (round(cx), round(cy))
        if key not in seen_cands:
            seen_cands[key] = (cx, cy)

    best_origin = None
    best_score = (0.0, 0, 0.0)
    for _, (cx, cy) in seen_cands.items():
        max_len, count = _score_junction(cx, cy)
        # When max_len ties, prefer junction closer to label over more connections
        proximity = -math.hypot(cx - label_x, cy - label_y)
        score = (max_len, proximity, count)
        if score > best_score:
            best_score = score
            best_origin = (cx, cy)

    if best_origin is None:
        return []

    # Step 3: collect tips — only lines going AWAY from the label
    origin_to_label = math.hypot(best_origin[0] - label_x, best_origin[1] - label_y)
    tips: list[LeaderTip] = []
    seen_tips: set[tuple[int, int]] = set()

    for lx1, ly1, lx2, ly2, llen, lw in lines:
        if abs(lw - 0.30) > 0.05 or llen < min_length:
            continue

        d1 = math.hypot(lx1 - best_origin[0], ly1 - best_origin[1])
        d2 = math.hypot(lx2 - best_origin[0], ly2 - best_origin[1])

        if d1 <= snap:
            far_x, far_y = lx2, ly2
        elif d2 <= snap:
            far_x, far_y = lx1, ly1
        else:
            continue

        # Skip tips that go back toward the label (the connecting stub)
        far_to_label = math.hypot(far_x - label_x, far_y - label_y)
        if far_to_label < origin_to_label:
            continue

        key = (round(far_x), round(far_y))
        if key in seen_tips:
            continue
        seen_tips.add(key)

        tips.append(LeaderTip(
            x=round(far_x, 1),
            y=round(far_y, 1),
            length=round(llen, 1),
        ))

    # Sort by distance ascending
    tips.sort(key=lambda t: t.length)
    return tips


def _determine_orientation(
    tips: list[LeaderTip],
    lines: list[tuple[float, float, float, float, float, float]],
) -> str:
    """Determine if members run in X (longitudinal) or Y (transverse) direction.

    For multi-tip members: if tips spread more in Y → members run in X direction.
    For single-tip members: find nearest structural line and check its angle.
    """
    if len(tips) >= 2:
        xs = [t.x for t in tips]
        ys = [t.y for t in tips]
        x_spread = max(xs) - min(xs)
        y_spread = max(ys) - min(ys)
        return "x" if y_spread > x_spread else "y"

    if len(tips) == 1:
        tip = tips[0]
        # Step 1: Check structural lines (w >= 0.35) weighted by length.
        # At joints where both H and V thick lines cross, the longer
        # line is the member the leader points to (e.g. column > beam stub).
        struct_horiz_len = 0.0
        struct_vert_len = 0.0
        for lx1, ly1, lx2, ly2, llen, lw in lines:
            if llen < 3 or lw < 0.35:
                continue
            d = _point_to_segment_dist(tip.x, tip.y, lx1, ly1, lx2, ly2)
            if d > 15:
                continue
            angle = math.degrees(
                math.atan2(abs(ly2 - ly1), abs(lx2 - lx1))
            )
            if angle < 30:
                struct_horiz_len += llen
            elif angle > 60:
                struct_vert_len += llen
        if struct_horiz_len > 0 and struct_vert_len > 0:
            # Both directions have structural lines — use length weighting
            return "x" if struct_horiz_len > struct_vert_len else "y"

        # Step 2: Fallback — count all lines including thin dashes (w=0.30).
        # This handles cases where the labeled member is a dashed line
        # (e.g. purlins w=0.30) near a solid frame column (w=0.42).
        horiz_count = 0
        vert_count = 0
        for lx1, ly1, lx2, ly2, llen, lw in lines:
            if llen < 3:
                continue
            d = _point_to_segment_dist(tip.x, tip.y, lx1, ly1, lx2, ly2)
            if d > 15:
                continue
            d1 = math.hypot(lx1 - tip.x, ly1 - tip.y)
            d2 = math.hypot(lx2 - tip.x, ly2 - tip.y)
            if (d1 < 3 or d2 < 3) and lw < 0.35:
                continue
            angle = math.degrees(
                math.atan2(abs(ly2 - ly1), abs(lx2 - lx1))
            )
            if angle < 30:
                horiz_count += 1
            elif angle > 60:
                vert_count += 1
        if horiz_count > 0 or vert_count > 0:
            return "x" if horiz_count > vert_count else "y"

    return ""


def _point_to_segment_dist(
    px: float, py: float,
    x1: float, y1: float, x2: float, y2: float,
) -> float:
    """Distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / len_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)
