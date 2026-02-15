"""Step F: Quality gates — validate extraction completeness."""

from .models import (
    Dimension,
    GateStatus,
    GridSystem,
    HeightParam,
    HeightType,
    QualityCheck,
    QualityReport,
    View,
    ViewType,
)


def run_quality_gates(
    views: list[View],
    grid: GridSystem | None,
    dimensions: list[Dimension],
    heights: list[HeightParam],
) -> QualityReport:
    """Run all quality checks and return a QualityReport."""
    checks = [
        _check_views_detected(views),
        _check_floor_plan_present(views),
        _check_grid_labels(grid),
        _check_grid_line_association(grid),
        _check_dimensions_found(dimensions),
        _check_heights_found(heights),
        _check_key_heights(heights),
    ]

    if any(c.status == GateStatus.FAIL for c in checks):
        overall = GateStatus.FAIL
    elif any(c.status == GateStatus.WARN for c in checks):
        overall = GateStatus.WARN
    else:
        overall = GateStatus.PASS

    return QualityReport(overall=overall, checks=checks)


def _check_views_detected(views: list[View]) -> QualityCheck:
    named = [v for v in views if v.view_type != ViewType.UNKNOWN]
    if len(named) >= 2:
        return QualityCheck(
            name="Views detected",
            status=GateStatus.PASS,
            message=f"{len(named)} views detected",
            detail=", ".join(v.view_type.value for v in named),
        )
    if len(named) == 1:
        return QualityCheck(
            name="Views detected",
            status=GateStatus.WARN,
            message="Only 1 view detected",
            detail=named[0].view_type.value,
        )
    return QualityCheck(
        name="Views detected",
        status=GateStatus.FAIL,
        message="No views detected",
        detail="Expected at least 平面図 and 立面図",
    )


def _check_floor_plan_present(views: list[View]) -> QualityCheck:
    has_floor = any(v.view_type == ViewType.FLOOR_PLAN for v in views)
    if has_floor:
        return QualityCheck(
            name="Floor plan (平面図)",
            status=GateStatus.PASS,
            message="平面図 found",
        )
    return QualityCheck(
        name="Floor plan (平面図)",
        status=GateStatus.FAIL,
        message="平面図 not found",
        detail="Floor plan is required for grid extraction",
    )


def _check_grid_labels(grid: GridSystem | None) -> QualityCheck:
    if grid is None:
        return QualityCheck(
            name="Grid labels",
            status=GateStatus.FAIL,
            message="No grid system detected",
            detail="Expected X1, X2, ..., Y1, Y2 labels",
        )
    x_count = len(grid.x_labels)
    y_count = len(grid.y_labels)
    if x_count >= 2 and y_count >= 1:
        return QualityCheck(
            name="Grid labels",
            status=GateStatus.PASS,
            message=f"{x_count} X-labels, {y_count} Y-labels",
        )
    return QualityCheck(
        name="Grid labels",
        status=GateStatus.WARN,
        message=f"Incomplete grid: {x_count} X-labels, {y_count} Y-labels",
        detail="Expected at least 2 X-labels and 1 Y-label",
    )


def _check_grid_line_association(grid: GridSystem | None) -> QualityCheck:
    if grid is None:
        return QualityCheck(
            name="Grid line association",
            status=GateStatus.FAIL,
            message="No grid to check",
        )
    all_labels = grid.x_labels + grid.y_labels
    with_lines = [l for l in all_labels if l.line is not None]
    total = len(all_labels)
    if not total:
        return QualityCheck(
            name="Grid line association",
            status=GateStatus.FAIL,
            message="No grid labels to associate",
        )
    ratio = len(with_lines) / total
    if ratio >= 0.8:
        return QualityCheck(
            name="Grid line association",
            status=GateStatus.PASS,
            message=f"{len(with_lines)}/{total} labels have lines",
        )
    return QualityCheck(
        name="Grid line association",
        status=GateStatus.WARN,
        message=f"Only {len(with_lines)}/{total} labels have associated lines",
    )


def _check_dimensions_found(dimensions: list[Dimension]) -> QualityCheck:
    count = len(dimensions)
    if count >= 5:
        return QualityCheck(
            name="Dimensions found",
            status=GateStatus.PASS,
            message=f"{count} dimensions extracted",
        )
    if count >= 1:
        return QualityCheck(
            name="Dimensions found",
            status=GateStatus.WARN,
            message=f"Only {count} dimensions found",
            detail="Expected at least 5 dimension values",
        )
    return QualityCheck(
        name="Dimensions found",
        status=GateStatus.FAIL,
        message="No dimensions found",
    )


def _check_heights_found(heights: list[HeightParam]) -> QualityCheck:
    count = len(heights)
    if count >= 1:
        types = [h.height_type.value for h in heights]
        return QualityCheck(
            name="Heights found",
            status=GateStatus.PASS,
            message=f"{count} height parameters found",
            detail=", ".join(types),
        )
    return QualityCheck(
        name="Heights found",
        status=GateStatus.FAIL,
        message="No height parameters found",
    )


def _check_key_heights(heights: list[HeightParam]) -> QualityCheck:
    has_eave = any(h.height_type == HeightType.EAVE_HEIGHT for h in heights)
    has_max = any(h.height_type == HeightType.MAX_HEIGHT for h in heights)
    if has_eave and has_max:
        return QualityCheck(
            name="Key heights (軒高 + 最高高さ)",
            status=GateStatus.PASS,
            message="Both 軒高 and 最高高さ found",
        )
    if has_eave or has_max:
        found = "軒高" if has_eave else "最高高さ"
        missing = "最高高さ" if has_eave else "軒高"
        return QualityCheck(
            name="Key heights (軒高 + 最高高さ)",
            status=GateStatus.WARN,
            message=f"Only {found} found, {missing} missing",
        )
    return QualityCheck(
        name="Key heights (軒高 + 最高高さ)",
        status=GateStatus.FAIL,
        message="Neither 軒高 nor 最高高さ found",
    )
