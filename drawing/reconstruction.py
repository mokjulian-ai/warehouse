"""Step 3: 2D → 3D Structural Reconstruction (構造3Dモデル生成).

Takes the MatchingResult from Step 2 and generates a deterministic 3D
wireframe model of the steel building with columns, rafters, ridge beam,
and purlins.
"""

import math
from collections import Counter

from .models import (
    BuildingEnvelope,
    GridSystem,
    MatchingResult,
    Member3D,
    MemberType,
    Point3D,
    StructuralModel,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def reconstruct_3d(
    matching: MatchingResult,
    grid: GridSystem | None = None,
) -> StructuralModel | None:
    """Run Step 3: convert Step 2 parameters into a 3D structural model.

    Returns None if essential parameters are missing.
    """
    # Validate required inputs
    span = matching.span
    length = matching.length
    eave = matching.eave_height
    ridge = matching.max_height
    pitch = matching.bay_pitch
    bay_count = matching.bay_count

    if any(v is None for v in (span, eave, ridge, pitch, bay_count)):
        return None

    # Derive length from pitch × bay_count if not directly available
    if length is None:
        length = pitch * bay_count

    # Step 3.1 — Build coordinate system
    x_positions = [i * pitch for i in range(bay_count + 1)]
    y_positions = _build_y_positions(span, grid)
    z_eave = eave
    z_ridge = ridge
    frame_count = bay_count + 1

    # Step 3.2–3.4 — Generate frame members (columns + rafters)
    members: list[Member3D] = []
    for i, xi in enumerate(x_positions):
        members.extend(
            _generate_frame_members(xi, i, y_positions, z_eave, z_ridge, span)
        )

    # Ridge beam
    y_ridge = span / 2.0
    members.append(_make_member(
        MemberType.RIDGE_BEAM,
        Point3D(x=0, y=y_ridge, z=z_ridge),
        Point3D(x=length, y=y_ridge, z=z_ridge),
        label="RB",
    ))

    # Step 3.5 — Purlins
    members.extend(
        _generate_purlins(x_positions, y_positions, z_eave, z_ridge, span)
    )

    # Step 3.6 — Member summary
    summary = dict(Counter(m.member_type.value for m in members))

    return StructuralModel(
        members=members,
        envelope=BuildingEnvelope(
            length=length,
            span=span,
            eave_height=z_eave,
            ridge_height=z_ridge,
        ),
        frame_count=frame_count,
        bay_count=bay_count,
        bay_pitch=pitch,
        x_grid_positions=x_positions,
        y_grid_positions=y_positions,
        member_summary=summary,
    )


# ---------------------------------------------------------------------------
# Coordinate system helpers
# ---------------------------------------------------------------------------


def _build_y_positions(span: float, grid: GridSystem | None) -> list[float]:
    """Build Y-axis positions from grid labels.

    Default: [0, span] (columns at outer walls only).
    If grid has 3+ Y-labels, add intermediate positions proportionally.
    """
    if grid and len(grid.y_labels) >= 3:
        # Sort by index and distribute proportionally across span
        sorted_labels = sorted(grid.y_labels, key=lambda gl: gl.index)
        positions = sorted(gl.position for gl in sorted_labels)
        p_min, p_max = positions[0], positions[-1]
        if p_max - p_min > 0:
            return [
                (p - p_min) / (p_max - p_min) * span
                for p in positions
            ]

    return [0.0, span]


# ---------------------------------------------------------------------------
# Member generation
# ---------------------------------------------------------------------------


def _compute_length_3d(start: Point3D, end: Point3D) -> float:
    """Euclidean distance between two 3D points."""
    dx = end.x - start.x
    dy = end.y - start.y
    dz = end.z - start.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _make_member(
    member_type: MemberType,
    start: Point3D,
    end: Point3D,
    label: str = "",
    frame_index: int | None = None,
) -> Member3D:
    """Create a Member3D with auto-computed length."""
    return Member3D(
        member_type=member_type,
        label=label,
        start=start,
        end=end,
        length=round(_compute_length_3d(start, end), 1),
        frame_index=frame_index,
    )


def _generate_frame_members(
    xi: float,
    frame_idx: int,
    y_positions: list[float],
    z_eave: float,
    z_ridge: float,
    span: float,
) -> list[Member3D]:
    """Generate columns and rafters for a single frame at X=xi."""
    members: list[Member3D] = []
    y_ridge = span / 2.0

    # Columns: one at each Y-grid position
    for j, yj in enumerate(y_positions):
        members.append(_make_member(
            MemberType.COLUMN,
            Point3D(x=xi, y=yj, z=0),
            Point3D(x=xi, y=yj, z=z_eave),
            label=f"C-F{frame_idx}-Y{j + 1}",
            frame_index=frame_idx,
        ))

    # Rafter: left eave → ridge
    members.append(_make_member(
        MemberType.RAFTER,
        Point3D(x=xi, y=y_positions[0], z=z_eave),
        Point3D(x=xi, y=y_ridge, z=z_ridge),
        label=f"R-F{frame_idx}-L",
        frame_index=frame_idx,
    ))

    # Rafter: ridge → right eave
    members.append(_make_member(
        MemberType.RAFTER,
        Point3D(x=xi, y=y_ridge, z=z_ridge),
        Point3D(x=xi, y=y_positions[-1], z=z_eave),
        label=f"R-F{frame_idx}-R",
        frame_index=frame_idx,
    ))

    return members


def _generate_purlins(
    x_positions: list[float],
    y_positions: list[float],
    z_eave: float,
    z_ridge: float,
    span: float,
    n_purlins_per_slope: int = 4,
) -> list[Member3D]:
    """Generate purlin members between adjacent frames.

    Purlins run longitudinally (X-direction) between frames,
    evenly spaced along each roof slope.
    """
    members: list[Member3D] = []
    y_ridge = span / 2.0
    y_left = y_positions[0]
    y_right = y_positions[-1]

    for bay_idx in range(len(x_positions) - 1):
        x_start = x_positions[bay_idx]
        x_end = x_positions[bay_idx + 1]

        # Left slope purlins (Y1 eave → ridge)
        for k in range(1, n_purlins_per_slope + 1):
            t = k / (n_purlins_per_slope + 1)
            y_p = y_left + t * (y_ridge - y_left)
            z_p = z_eave + t * (z_ridge - z_eave)
            members.append(_make_member(
                MemberType.PURLIN,
                Point3D(x=x_start, y=y_p, z=z_p),
                Point3D(x=x_end, y=y_p, z=z_p),
                label=f"P-B{bay_idx}-L{k}",
            ))

        # Right slope purlins (ridge → Y2 eave)
        for k in range(1, n_purlins_per_slope + 1):
            t = k / (n_purlins_per_slope + 1)
            y_p = y_ridge + t * (y_right - y_ridge)
            z_p = z_ridge + t * (z_eave - z_ridge)
            members.append(_make_member(
                MemberType.PURLIN,
                Point3D(x=x_start, y=y_p, z=z_p),
                Point3D(x=x_end, y=y_p, z=z_p),
                label=f"P-B{bay_idx}-R{k}",
            ))

    return members
