"""Step 2: Cross-view matching using grid labels as universal address (通り芯で紐付け).

Links plan ↔ elevation ↔ section views through shared grid labels,
and anchors extracted dimensions/heights to specific grid positions.
"""

import re

from .grids import extract_per_view_grids
from .models import (
    AnchoredParam,
    Dimension,
    DimensionType,
    FrameLink,
    GateStatus,
    GridSystem,
    HeightParam,
    HeightType,
    MatchingResult,
    QualityCheck,
    View,
    ViewGridInfo,
    ViewType,
)
from .primitives import is_horizontal, is_vertical

# Pattern to extract grid side from elevation title: "立面図 (Y1通り)"
SIDE_PATTERN = re.compile(
    r"[（(〈<]?\s*([XYxy\uff38\uff39\uff58\uff59]\S*?)通り\s*[）)〉>]?"
)

PT_TO_MM = 25.4 / 72.0  # 1 PDF point = 0.3528 mm


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_matching(
    views: list[View],
    grid: GridSystem | None,
    dimensions: list[Dimension],
    heights: list[HeightParam],
    page_rotation: int = 0,
) -> MatchingResult | None:
    """Run Step 2: cross-view matching and parameter anchoring."""
    if not grid:
        return None

    # 2.1: Canonical grid source
    canonical_source = grid.source_view

    # 2.2-2.3: Per-view grid info with elevation side detection
    view_grid_info = _build_view_grid_info(views, page_rotation)

    # 2.4-2.5: Frame links (X-grid positions across views)
    frame_links = _build_frame_links(grid, view_grid_info)

    # Find plan view scale for coordinate → mm conversion
    plan_view = _find_plan_view(views)
    scale = _parse_scale(plan_view.scale if plan_view else None)

    # 2.6: Anchor parameters to grid
    anchored = _anchor_parameters(
        grid, dimensions, heights, scale, plan_view, page_rotation,
        views=views, view_grid_info=view_grid_info,
    )

    # Extract convenience top-level values
    span = _get_param(anchored, "span")
    length = _get_param(anchored, "length")
    pitch = _get_param(anchored, "bay_pitch")
    bay_count_f = _get_param(anchored, "bay_count")
    eave = _get_param(anchored, "eave_height")
    max_h = _get_param(anchored, "max_height")

    # 2.7: Consistency checks
    checks = _run_consistency_checks(
        grid, view_grid_info, frame_links,
        span, length, pitch, bay_count_f,
    )

    return MatchingResult(
        canonical_grid_source=canonical_source,
        view_grid_info=view_grid_info,
        frame_links=frame_links,
        anchored_params=anchored,
        consistency_checks=checks,
        span=span,
        length=length,
        bay_pitch=pitch,
        bay_count=int(bay_count_f) if bay_count_f else None,
        eave_height=eave,
        max_height=max_h,
    )


# ---------------------------------------------------------------------------
# 2.2-2.3  Per-view grid info + elevation side detection
# ---------------------------------------------------------------------------


def _build_view_grid_info(
    views: list[View],
    page_rotation: int = 0,
) -> list[ViewGridInfo]:
    """Build per-view grid info with side detection for elevations."""
    per_view = extract_per_view_grids(views, page_rotation=page_rotation)
    result: list[ViewGridInfo] = []

    for i, view in enumerate(views):
        x_labels_raw, y_labels_raw = per_view.get(i, ([], []))
        x_label_names = sorted(set(gl.label for gl in x_labels_raw))
        y_label_names = sorted(set(gl.label for gl in y_labels_raw))

        # Detect grid side for elevation views
        side = None
        if view.view_type == ViewType.ELEVATION:
            side = _detect_elevation_side(view.title_text)
            # Infer from grid labels if title doesn't specify
            if not side:
                side = _infer_elevation_side(x_label_names, y_label_names)

        result.append(ViewGridInfo(
            view_index=i,
            view_type=view.view_type,
            view_title=view.title_text,
            grid_side=side,
            x_labels=x_label_names,
            y_labels=y_label_names,
        ))

    return result


def _detect_elevation_side(title_text: str) -> str | None:
    """Extract grid side from elevation title, e.g. '立面図 (Y1通り)' → 'Y1'."""
    m = SIDE_PATTERN.search(title_text)
    if not m:
        return None
    side = m.group(1).upper()
    # Normalize full-width characters
    side = side.replace("\uff38", "X").replace("\uff39", "Y")
    side = side.replace("\uff58", "X").replace("\uff59", "Y")
    return side


def _infer_elevation_side(
    x_labels: list[str],
    y_labels: list[str],
) -> str | None:
    """Infer which side an elevation faces from its grid labels.

    If elevation has X-grid labels → it shows the building along X-direction
    → it faces a Y-side (Y1 or Y2).
    If elevation has Y-grid labels → it faces an X-side (X1 or Xn+1).
    """
    has_x = len(x_labels) > 0
    has_y = len(y_labels) > 0
    if has_x and not has_y:
        return "Y-side"
    if has_y and not has_x:
        return "X-side"
    return None


# ---------------------------------------------------------------------------
# 2.4-2.5  Frame links
# ---------------------------------------------------------------------------


def _build_frame_links(
    grid: GridSystem,
    view_grid_info: list[ViewGridInfo],
) -> list[FrameLink]:
    """Build cross-view frame links for each X-grid label."""
    # Canonical X positions from grid
    all_x_labels = {gl.label: gl.position for gl in grid.x_labels}

    # Find which elevation sides contain each X label
    elevation_info: dict[str, list[str]] = {}  # x_label → [side1, side2, ...]
    for vgi in view_grid_info:
        if vgi.view_type == ViewType.ELEVATION and vgi.grid_side:
            for xl in vgi.x_labels:
                if xl not in elevation_info:
                    elevation_info[xl] = []
                if vgi.grid_side not in elevation_info[xl]:
                    elevation_info[xl].append(vgi.grid_side)

    links: list[FrameLink] = []
    for label in sorted(all_x_labels.keys(), key=lambda la: all_x_labels[la]):
        links.append(FrameLink(
            x_label=label,
            plan_x_position=all_x_labels[label],
            in_elevation_sides=sorted(elevation_info.get(label, [])),
        ))

    return links


# ---------------------------------------------------------------------------
# 2.6  Anchor parameters to grid
# ---------------------------------------------------------------------------


def _anchor_parameters(
    grid: GridSystem,
    dimensions: list[Dimension],
    heights: list[HeightParam],
    scale: float | None,
    plan_view: View | None = None,
    page_rotation: int = 0,
    views: list[View] | None = None,
    view_grid_info: list[ViewGridInfo] | None = None,
) -> list[AnchoredParam]:
    """Anchor dimensions and heights to grid positions."""
    params: list[AnchoredParam] = []
    swapped = page_rotation in (90, 270)

    # Label names for anchoring — sort by grid index (X1, X7, Xn+1),
    # not by page position (which mixes labels from different views).
    y_by_index = sorted(grid.y_labels, key=lambda gl: gl.index)
    x_by_index = sorted(grid.x_labels, key=lambda gl: gl.index)
    y_first_label = y_by_index[0].label if y_by_index else None
    y_last_label = y_by_index[-1].label if y_by_index else None
    x_first_label = x_by_index[0].label if x_by_index else None
    x_last_label = x_by_index[-1].label if x_by_index else None

    # --- Span ---
    span_found = False

    # 1. Try grid line matching in plan view
    if plan_view and scale:
        span_result = _match_grid_distance(
            plan_view, dimensions, scale,
            is_y_axis=True, swapped=swapped,
        )
        if span_result:
            dim, dist_mm = span_result
            # Check for multi-span building: plan may only show half-span
            multi = _check_multi_span(
                dim.value, dimensions, views, view_grid_info,
            )
            if multi:
                params.append(AnchoredParam(
                    name="span",
                    value=multi.value,
                    anchor_from=y_first_label,
                    anchor_to=y_last_label,
                    source_view=multi.source_view,
                    raw_text=multi.raw_text,
                ))
            else:
                params.append(AnchoredParam(
                    name="span",
                    value=dim.value,
                    anchor_from=y_first_label,
                    anchor_to=y_last_label,
                    source_view=dim.source_view,
                    raw_text=dim.raw_text,
                ))
            span_found = True

    # 2. Try finding span from cross-views (section/X-side elevations)
    if not span_found and views and view_grid_info:
        span_dim = _find_span_from_cross_views(
            dimensions, views, view_grid_info,
        )
        if span_dim:
            params.append(AnchoredParam(
                name="span",
                value=span_dim.value,
                anchor_from=y_first_label,
                anchor_to=y_last_label,
                source_view=span_dim.source_view,
                raw_text=span_dim.raw_text,
            ))
            span_found = True

    # 3. Fallback: compute from grid positions
    if not span_found and len(y_by_index) >= 2 and scale:
        y_positions = sorted(gl.position for gl in y_by_index)
        span_pts = abs(y_positions[-1] - y_positions[0])
        if span_pts > 1.0:
            span_mm = span_pts * PT_TO_MM * scale
            params.append(AnchoredParam(
                name="span",
                value=round(span_mm),
                anchor_from=y_first_label,
                anchor_to=y_last_label,
                computed=True,
            ))

    # --- Length via repeat dimensions or line-pair matching ---
    span_val = _get_param(params, "span")
    length_found = False

    if plan_view and scale:
        # First: try grid line distance matching for X-axis
        length_result = _match_grid_distance(
            plan_view, dimensions, scale,
            is_y_axis=False, swapped=swapped,
        )
        if length_result:
            dim, dist_mm = length_result
            # Reject if this matches the span value (wrong direction)
            if span_val and abs(dim.value - span_val) / max(span_val, 1) < 0.03:
                length_result = None

        if length_result:
            dim, dist_mm = length_result
            params.append(AnchoredParam(
                name="length",
                value=dim.value,
                anchor_from=x_first_label,
                anchor_to=x_last_label,
                source_view=dim.source_view,
                raw_text=dim.raw_text,
            ))
            length_found = True

    # Second: compute from repeat dimension chain (e.g. "2000×n")
    # Sum dimension segments in plan/roof views that are multiples of pitch
    if not length_found:
        length_from_repeat = _compute_length_from_repeat(dimensions, views)
        if length_from_repeat:
            total_val, raw_text = length_from_repeat
            params.append(AnchoredParam(
                name="length",
                value=total_val,
                anchor_from=x_first_label,
                anchor_to=x_last_label,
                raw_text=raw_text,
                computed=True,
            ))
            length_found = True

    # Third: fallback from grid X positions
    if not length_found and len(x_by_index) >= 2 and scale:
        x_positions = sorted(gl.position for gl in x_by_index)
        length_pts = abs(x_positions[-1] - x_positions[0])
        if length_pts > 1.0:
            length_mm = length_pts * PT_TO_MM * scale
            params.append(AnchoredParam(
                name="length",
                value=round(length_mm),
                anchor_from=x_first_label,
                anchor_to=x_last_label,
                computed=True,
            ))

    # --- Bay pitch ---
    pitch_dims = [d for d in dimensions if d.dim_type == DimensionType.PITCH]
    if pitch_dims:
        params.append(AnchoredParam(
            name="bay_pitch",
            value=pitch_dims[0].value,
            source_view=pitch_dims[0].source_view,
            raw_text=pitch_dims[0].raw_text,
        ))
    elif len(grid.x_labels) >= 3 and scale:
        # Compute from consecutive X grid spacing
        x_sorted = sorted(grid.x_labels, key=lambda gl: gl.position)
        spacings: list[float] = []
        for i in range(1, len(x_sorted)):
            # Only use numeric labels (index < 900 means not symbolic like Xn+1)
            if x_sorted[i].index < 900 and x_sorted[i - 1].index < 900:
                sp = abs(x_sorted[i].position - x_sorted[i - 1].position)
                spacings.append(sp * PT_TO_MM * scale)
        if spacings:
            avg = sum(spacings) / len(spacings)
            # If all spacings are uniform (within 10%), use as pitch
            if all(abs(s - avg) / max(avg, 1) < 0.1 for s in spacings):
                params.append(AnchoredParam(
                    name="bay_pitch",
                    value=round(avg),
                    computed=True,
                ))

    # --- Bay count ---
    # Resolve length and pitch values first for computation
    length_val = _get_param(params, "length")
    pitch_val = _get_param(params, "bay_pitch")

    repeat_dims = [
        d for d in dimensions
        if d.dim_type == DimensionType.REPEAT and d.repeat_count
    ]
    if repeat_dims:
        params.append(AnchoredParam(
            name="bay_count",
            value=float(repeat_dims[0].repeat_count),
            raw_text=repeat_dims[0].raw_text,
        ))
    elif pitch_val and pitch_val > 0:
        # Try length/pitch first
        bay_count_found = False
        if length_val:
            count = length_val / pitch_val
            if abs(count - round(count)) < 0.15:
                params.append(AnchoredParam(
                    name="bay_count",
                    value=float(round(count)),
                    computed=True,
                ))
                bay_count_found = True

        # Fallback: find the largest dimension that divides cleanly by pitch
        # (the grid length may differ from total building length)
        if not bay_count_found:
            for d in sorted(dimensions, key=lambda x: -x.value):
                if d.dim_type != DimensionType.SINGLE:
                    continue
                if d.value < pitch_val * 2:
                    continue
                if length_val and d.value >= length_val:
                    continue
                c = d.value / pitch_val
                if abs(c - round(c)) < 0.05:
                    params.append(AnchoredParam(
                        name="bay_count",
                        value=float(round(c)),
                        raw_text=f"from {d.raw_text}/{pitch_dims[0].raw_text}" if pitch_dims else "",
                        computed=True,
                    ))
                    bay_count_found = True
                    break

    # --- Heights ---
    for h in heights:
        if h.height_type == HeightType.EAVE_HEIGHT and h.value is not None:
            params.append(AnchoredParam(
                name="eave_height",
                value=h.value,
                source_view=h.source_view,
                raw_text=h.raw_text,
            ))
        elif h.height_type == HeightType.MAX_HEIGHT and h.value is not None:
            params.append(AnchoredParam(
                name="max_height",
                value=h.value,
                source_view=h.source_view,
                raw_text=h.raw_text,
            ))

    return params


def _find_span_from_cross_views(
    dimensions: list[Dimension],
    views: list[View],
    view_grid_info: list[ViewGridInfo],
) -> Dimension | None:
    """Find span dimension from section or X-side elevation views.

    Section views and X-side elevations show Y-direction horizontally.
    The largest single dimension in such a view (between Y-grid labels)
    is the span (Y1↔Y2 distance).
    """
    # Find view indices that show Y-direction (have Y-grid labels)
    y_view_indices: set[int] = set()
    for vi in view_grid_info:
        if not vi.y_labels:
            continue
        if vi.view_type in (ViewType.SECTION, ViewType.ELEVATION):
            y_view_indices.add(vi.view_index)

    if not y_view_indices:
        return None

    # Find the largest single dimension in these views
    best: Dimension | None = None
    for d in dimensions:
        if d.dim_type != DimensionType.SINGLE:
            continue
        if d.value < 1000:  # Too small for a building span
            continue
        if d.source_view not in (ViewType.SECTION, ViewType.ELEVATION):
            continue
        # Check if the dimension text falls within a Y-direction view
        for vi_idx in y_view_indices:
            view = views[vi_idx]
            if view.region.contains(d.text_span.center):
                if best is None or d.value > best.value:
                    best = d
                break

    return best


def _compute_length_from_repeat(
    dimensions: list[Dimension],
    views: list[View] | None = None,
) -> tuple[float, str] | None:
    """Compute total length from repeat dimension chain (e.g. '2000×n').

    The length direction often has multiple dimension segments
    (e.g. 12000 + 4000 + 14000 = 30000) between consecutive grid labels.
    Sums all single dimensions in plan/roof views that are clean multiples
    of the bay pitch.
    Returns (total_value, description_string) or None.
    """
    # Find pitch from repeat or pitch dimensions
    pitch_val: float | None = None
    for d in dimensions:
        if d.dim_type in (DimensionType.REPEAT, DimensionType.PITCH):
            pitch_val = d.value
            break
    if not pitch_val:
        return None

    # If repeat dimension has an explicit numeric count, use it directly
    for d in dimensions:
        if d.dim_type == DimensionType.REPEAT and d.repeat_count:
            total = pitch_val * d.repeat_count
            return (total, d.raw_text)

    # Sum dimension segments that are multiples of pitch.
    # These form a chain: X1→X7=12000, X7→X9=4000, X9→Xn+1=14000 etc.
    # Don't filter by view type — the segments may appear in elevation views
    # (especially with rotated PDFs where view regions shift).
    seen_values: dict[float, bool] = {}  # deduplicate by value
    segments: list[float] = []
    for d in dimensions:
        if d.dim_type != DimensionType.SINGLE:
            continue
        if d.value < pitch_val * 2:
            continue
        n = d.value / pitch_val
        if abs(n - round(n)) < 0.05:
            if d.value not in seen_values:
                seen_values[d.value] = True
                segments.append(d.value)

    if segments:
        total = sum(segments)
        n_total = round(total / pitch_val)
        parts = "+".join(f"{int(s)}" for s in segments)
        return (total, f"{parts}={int(total)} ({n_total}×@{int(pitch_val)})")

    return None


def _check_multi_span(
    half_span: float,
    dimensions: list[Dimension],
    views: list[View] | None,
    view_grid_info: list[ViewGridInfo] | None,
) -> Dimension | None:
    """Detect multi-span building where grid lines show half-span only.

    For a 2-span building (e.g. 7500+7500=15000mm), the plan view may only
    have grid lines for one span.  Check if 2× or 3× the detected grid
    distance exists as a dimension, confirmed in section or X-side elevation
    views (which display Y-direction).
    """
    for n in (2, 3):
        candidate = half_span * n
        for d in dimensions:
            if d.dim_type != DimensionType.SINGLE:
                continue
            if abs(d.value - candidate) / max(candidate, 1) > 0.03:
                continue
            # Found a matching dimension — verify it appears in a cross-view
            # that shows Y-direction (section or X-side elevation).
            if d.source_view == ViewType.SECTION:
                return d
            if d.source_view == ViewType.ELEVATION and views and view_grid_info:
                # Check if the elevation containing this text is X-side
                # (has Y-grid labels → shows Y-direction horizontally)
                for vi in view_grid_info:
                    if vi.view_type != ViewType.ELEVATION:
                        continue
                    if not vi.y_labels:
                        continue
                    # Check if dimension text falls within this view's region
                    view = views[vi.view_index]
                    if view.region.contains(d.text_span.center):
                        return d
    return None


def _find_matching_dimension(
    expected_mm: float,
    dimensions: list[Dimension],
    tolerance: float = 0.05,
) -> Dimension | None:
    """Find a dimension value within tolerance of expected value."""
    best: Dimension | None = None
    best_diff = float("inf")
    for d in dimensions:
        if d.dim_type not in (DimensionType.SINGLE, DimensionType.REPEAT):
            continue
        diff = abs(d.value - expected_mm) / max(expected_mm, 1)
        if diff < tolerance and diff < best_diff:
            best = d
            best_diff = diff
    return best


# ---------------------------------------------------------------------------
# 2.7  Consistency checks
# ---------------------------------------------------------------------------


def _run_consistency_checks(
    grid: GridSystem,
    view_grid_info: list[ViewGridInfo],
    frame_links: list[FrameLink],
    span: float | None,
    length: float | None,
    pitch: float | None,
    bay_count: float | None,
) -> list[QualityCheck]:
    """Run Step 2 consistency validations."""
    checks: list[QualityCheck] = []

    plan_views = [v for v in view_grid_info if v.view_type == ViewType.FLOOR_PLAN]
    elev_views = [v for v in view_grid_info if v.view_type == ViewType.ELEVATION]

    # Check 1: Grid label continuity (plan ↔ elevation)
    if plan_views and elev_views:
        plan_x = set(plan_views[0].x_labels) if plan_views else set()
        elev_x: set[str] = set()
        for ev in elev_views:
            elev_x.update(ev.x_labels)

        common = plan_x & elev_x
        if len(common) >= 2:
            checks.append(QualityCheck(
                name="Grid continuity (plan↔elevation)",
                status=GateStatus.PASS,
                message=f"{len(common)} shared X-labels: {', '.join(sorted(common))}",
            ))
        elif len(common) == 1:
            checks.append(QualityCheck(
                name="Grid continuity (plan↔elevation)",
                status=GateStatus.WARN,
                message=f"Only 1 shared X-label: {', '.join(common)}",
            ))
        else:
            checks.append(QualityCheck(
                name="Grid continuity (plan↔elevation)",
                status=GateStatus.FAIL,
                message="No shared X-labels between plan and elevation",
            ))

    # Check 2: Pitch × bay count ≈ length
    if pitch and bay_count and length:
        expected_length = pitch * bay_count
        diff = abs(expected_length - length) / max(length, 1)
        if diff < 0.05:
            checks.append(QualityCheck(
                name="Pitch × count = length",
                status=GateStatus.PASS,
                message=f"{pitch:.0f} × {bay_count:.0f} = {expected_length:.0f} ≈ {length:.0f}",
            ))
        else:
            checks.append(QualityCheck(
                name="Pitch × count = length",
                status=GateStatus.WARN,
                message=f"{pitch:.0f} × {bay_count:.0f} = {expected_length:.0f} ≠ {length:.0f}",
            ))

    # Check 3: Elevation sides identified
    sides = set()
    for ev in elev_views:
        if ev.grid_side:
            sides.add(ev.grid_side)

    if len(sides) >= 2:
        checks.append(QualityCheck(
            name="Elevation sides identified",
            status=GateStatus.PASS,
            message=f"Sides: {', '.join(sorted(sides))}",
        ))
    elif len(sides) == 1:
        checks.append(QualityCheck(
            name="Elevation sides identified",
            status=GateStatus.WARN,
            message=f"Only 1 side: {', '.join(sides)}",
        ))
    elif elev_views:
        checks.append(QualityCheck(
            name="Elevation sides identified",
            status=GateStatus.FAIL,
            message="No elevation sides detected from titles",
        ))

    # Check 4: Key building parameters present
    params_found: list[str] = []
    params_missing: list[str] = []
    for name, val in [
        ("span", span), ("length", length),
        ("pitch", pitch), ("bay_count", bay_count),
    ]:
        (params_found if val is not None else params_missing).append(name)

    if not params_missing:
        checks.append(QualityCheck(
            name="Building parameters",
            status=GateStatus.PASS,
            message=f"All found: {', '.join(params_found)}",
        ))
    elif len(params_found) >= 2:
        checks.append(QualityCheck(
            name="Building parameters",
            status=GateStatus.WARN,
            message=f"Found: {', '.join(params_found)}; Missing: {', '.join(params_missing)}",
        ))
    else:
        checks.append(QualityCheck(
            name="Building parameters",
            status=GateStatus.FAIL,
            message=f"Missing: {', '.join(params_missing)}",
        ))

    return checks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match_grid_distance(
    view: View,
    dimensions: list[Dimension],
    scale: float,
    is_y_axis: bool,
    swapped: bool,
    min_length: float = 100.0,
    tolerance: float = 0.03,
) -> tuple[Dimension, float] | None:
    """Find a dimension value that matches a pairwise distance between grid lines.

    Looks at all pairs of long lines in the view, computes the real-world
    distance for each pair, and finds the best match among extracted dimensions.
    Returns (matched_dimension, distance_mm) or None.
    """
    positions = _find_distinct_grid_lines(
        view, is_y_axis=is_y_axis, swapped=swapped, min_length=min_length,
    )
    if len(positions) < 2:
        return None

    # Try all pairs — prefer the largest matching distance
    best: tuple[Dimension, float] | None = None
    best_value = 0.0

    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            dist_pts = abs(positions[j] - positions[i])
            dist_mm = dist_pts * PT_TO_MM * scale

            dim_match = _find_matching_dimension(dist_mm, dimensions, tolerance)
            if dim_match and dim_match.value > best_value:
                best = (dim_match, dist_mm)
                best_value = dim_match.value

    return best


def _find_distinct_grid_lines(
    view: View,
    is_y_axis: bool,
    swapped: bool,
    min_length: float = 50.0,
    cluster_tol: float = 5.0,
) -> list[float]:
    """Find distinct long grid line positions in a view.

    For Y-axis: finds vertical (or horizontal if swapped) lines.
    Returns sorted unique position values.
    """
    positions: list[float] = []

    for ln in view.lines:
        if ln.length < min_length:
            continue

        if is_y_axis:
            # Y-grid lines: vertical in visual → vertical in mediabox (normal)
            #                                  → horizontal in mediabox (swapped)
            if swapped:
                if not is_vertical(ln, tolerance_deg=10.0):
                    continue
                positions.append((ln.p1.x + ln.p2.x) / 2)
            else:
                if not is_horizontal(ln, tolerance_deg=10.0):
                    continue
                positions.append((ln.p1.y + ln.p2.y) / 2)
        else:
            # X-grid lines: vertical in visual → horizontal in mediabox (swapped)
            if swapped:
                if not is_horizontal(ln, tolerance_deg=10.0):
                    continue
                positions.append((ln.p1.y + ln.p2.y) / 2)
            else:
                if not is_vertical(ln, tolerance_deg=10.0):
                    continue
                positions.append((ln.p1.x + ln.p2.x) / 2)

    if not positions:
        return []

    # Cluster nearby positions
    positions.sort()
    clusters: list[float] = [positions[0]]
    for p in positions[1:]:
        if abs(p - clusters[-1]) > cluster_tol:
            clusters.append(p)
        else:
            # Average with current cluster
            clusters[-1] = (clusters[-1] + p) / 2

    return sorted(clusters)


def _find_plan_view(views: list[View]) -> View | None:
    """Find the floor plan view."""
    for v in views:
        if v.view_type == ViewType.FLOOR_PLAN:
            return v
    return None


def _parse_scale(scale_str: str | None) -> float | None:
    """Parse '1/150' → 150.0."""
    if not scale_str:
        return None
    m = re.match(r"1\s*/\s*(\d+)", scale_str)
    return float(m.group(1)) if m else None


def _get_param(params: list[AnchoredParam], name: str) -> float | None:
    """Get first param value by name."""
    for p in params:
        if p.name == name:
            return p.value
    return None
