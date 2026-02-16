"""Step C: Extract grid system (通り芯) per view."""

import re

from .models import GridAxis, GridLabel, GridSystem, Line, TextSpan, View, ViewType
from .primitives import is_horizontal, is_vertical, point_to_line_distance

# Match X1, X2, ..., Y1, Y2 — half-width and full-width digits
GRID_NUMERIC_PATTERN = re.compile(
    r"^([XxYy\uff38\uff39\uff58\uff59])[\s\u3000]*(\d{1,2})$"
)
# Match symbolic labels: Xn+1, Xn, etc.
GRID_SYMBOLIC_PATTERN = re.compile(
    r"^([XxYy\uff38\uff39\uff58\uff59])[\s\u3000]*(n\+?\d*|n)$", re.IGNORECASE
)

MIN_GRID_LINE_LENGTH = 50.0


def extract_per_view_grids(
    views: list[View],
    page_rotation: int = 0,
) -> dict[int, tuple[list[GridLabel], list[GridLabel]]]:
    """Extract grid labels per view. Returns {view_index: (x_labels, y_labels)}."""
    result: dict[int, tuple[list[GridLabel], list[GridLabel]]] = {}
    for i, view in enumerate(views):
        x_labels, y_labels = _extract_from_view(view, page_rotation)
        if x_labels or y_labels:
            result[i] = (x_labels, y_labels)
    return result


def extract_grid_system(
    views: list[View],
    page_rotation: int = 0,
) -> GridSystem | None:
    """Extract grid from views. Merges results from all matching views."""
    # Collect labels from priority views
    priority = [ViewType.FLOOR_PLAN, ViewType.ELEVATION]
    all_x: list[GridLabel] = []
    all_y: list[GridLabel] = []
    source_view = ViewType.UNKNOWN

    # Try priority views first
    for target_type in priority:
        for view in views:
            if view.view_type == target_type:
                x_labels, y_labels = _extract_from_view(view, page_rotation)
                if x_labels or y_labels:
                    if source_view == ViewType.UNKNOWN:
                        source_view = view.view_type
                    all_x.extend(x_labels)
                    all_y.extend(y_labels)

    # Fallback to all views if nothing found
    if not all_x and not all_y:
        for view in views:
            x_labels, y_labels = _extract_from_view(view, page_rotation)
            if x_labels or y_labels:
                if source_view == ViewType.UNKNOWN:
                    source_view = view.view_type
                all_x.extend(x_labels)
                all_y.extend(y_labels)

    if not all_x and not all_y:
        return None

    # Deduplicate by label name, keeping first occurrence
    seen: set[str] = set()
    dedup_x: list[GridLabel] = []
    for l in all_x:
        if l.label not in seen:
            seen.add(l.label)
            dedup_x.append(l)
    dedup_y: list[GridLabel] = []
    for l in all_y:
        if l.label not in seen:
            seen.add(l.label)
            dedup_y.append(l)

    dedup_x.sort(key=lambda l: l.position)
    dedup_y.sort(key=lambda l: l.position)

    return GridSystem(
        x_labels=dedup_x,
        y_labels=dedup_y,
        source_view=source_view,
    )


def _extract_from_view(
    view: View,
    page_rotation: int = 0,
) -> tuple[list[GridLabel], list[GridLabel]]:
    """Extract grid labels and associate with lines from a single view."""
    label_matches = _find_grid_labels(view.texts)
    if not label_matches:
        return [], []

    swapped = page_rotation in (90, 270)

    x_labels: list[GridLabel] = []
    y_labels: list[GridLabel] = []

    for axis, index, label_text, text_span in label_matches:
        line = _associate_label_to_line(axis, text_span, view.lines, swapped)
        position = _label_position(axis, text_span, line, swapped)
        label = GridLabel(
            axis=axis,
            label=label_text,
            index=index,
            position=position,
            text_span=text_span,
            line=line,
        )
        if axis == GridAxis.X:
            x_labels.append(label)
        else:
            y_labels.append(label)

    return x_labels, y_labels


def _find_grid_labels(
    texts: list[TextSpan],
) -> list[tuple[GridAxis, int, str, TextSpan]]:
    """Find texts matching grid label patterns. Returns (axis, index, label, span)."""
    results: list[tuple[GridAxis, int, str, TextSpan]] = []
    seen: set[str] = set()

    for text_span in texts:
        clean = text_span.text.strip()

        # Try numeric pattern first (X1, X2, Y1, etc.)
        m = GRID_NUMERIC_PATTERN.match(clean)
        if m:
            axis_char = _normalize_axis(m.group(1))
            axis = GridAxis.X if axis_char == "X" else GridAxis.Y
            index = int(m.group(2))
            label = f"{axis_char}{index}"
            if label not in seen:
                seen.add(label)
                results.append((axis, index, label, text_span))
            continue

        # Try symbolic pattern (Xn+1, Xn, etc.)
        m = GRID_SYMBOLIC_PATTERN.match(clean)
        if m:
            axis_char = _normalize_axis(m.group(1))
            axis = GridAxis.X if axis_char == "X" else GridAxis.Y
            suffix = m.group(2)  # "n+1", "n", etc.
            label = f"{axis_char}{suffix}"
            # Use a high index for sorting (symbolic labels are typically at the end)
            index = 999
            if label not in seen:
                seen.add(label)
                results.append((axis, index, label, text_span))
            continue

    return results


def _normalize_axis(char: str) -> str:
    """Normalize axis character to half-width uppercase."""
    c = char.upper()
    if c in ("\uff38", "\uff58"):
        return "X"
    if c in ("\uff39", "\uff59"):
        return "Y"
    return c


def _associate_label_to_line(
    axis: GridAxis,
    text_span: TextSpan,
    lines: list[Line],
    axes_swapped: bool = False,
) -> Line | None:
    """Find the nearest long line of correct orientation for a grid label.

    When axes_swapped=True (rotation 90/270), X-grid lines appear horizontal
    in mediabox and Y-grid lines appear vertical — the opposite of no rotation.
    """
    candidates: list[tuple[float, Line]] = []

    for ln in lines:
        if ln.length < MIN_GRID_LINE_LENGTH:
            continue

        if axes_swapped:
            # Rotated: X-grid → horizontal in mediabox, Y-grid → vertical
            if axis == GridAxis.X and not is_horizontal(ln, tolerance_deg=10.0):
                continue
            if axis == GridAxis.Y and not is_vertical(ln, tolerance_deg=10.0):
                continue
        else:
            # Normal: X-grid → vertical, Y-grid → horizontal
            if axis == GridAxis.X and not is_vertical(ln, tolerance_deg=10.0):
                continue
            if axis == GridAxis.Y and not is_horizontal(ln, tolerance_deg=10.0):
                continue

        d = point_to_line_distance(text_span.center, ln)
        candidates.append((d, ln))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    best_dist, best_line = candidates[0]
    if best_dist > 100.0:
        return None

    return best_line


def _label_position(
    axis: GridAxis,
    text_span: TextSpan,
    line: Line | None,
    axes_swapped: bool = False,
) -> float:
    """Determine position coordinate for a grid label.

    When axes_swapped=True (rotation 90/270):
      X-axis position uses mediabox Y (= visual X for rot 270)
      Y-axis position uses mediabox X (= visual Y for rot 270)
    """
    if axes_swapped:
        if line:
            if axis == GridAxis.X:
                return (line.p1.y + line.p2.y) / 2
            else:
                return (line.p1.x + line.p2.x) / 2
        if axis == GridAxis.X:
            return text_span.center.y
        return text_span.center.x
    else:
        if line:
            if axis == GridAxis.X:
                return (line.p1.x + line.p2.x) / 2
            else:
                return (line.p1.y + line.p2.y) / 2
        if axis == GridAxis.X:
            return text_span.center.x
        return text_span.center.y
