"""Step 4: Member Grouping & Quantity Takeoff (積算).

Takes the StructuralModel from Step 3 and groups members by type and
length into a quantity takeoff table suitable for estimation.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .models import (
    MemberGroup,
    MemberType,
    QuantityTakeoff,
    StructuralModel,
)

# Type ordering for sorted output
_TYPE_ORDER = {
    MemberType.COLUMN: 0,
    MemberType.RAFTER: 1,
    MemberType.RIDGE_BEAM: 2,
    MemberType.PURLIN: 3,
}


def compute_quantity_takeoff(
    model: StructuralModel,
    tolerance: float = 10.0,
) -> QuantityTakeoff:
    """Run Step 4: group members and produce quantity takeoff.

    Args:
        model: The 3D structural model from Step 3.
        tolerance: Length tolerance in mm for grouping.

    Returns:
        QuantityTakeoff with grouped member quantities.
    """
    # Group by (member_type, rounded_length)
    buckets: dict[tuple[MemberType, float], list[str]] = defaultdict(list)

    for member in model.members:
        rounded = _round_to(member.length, tolerance)
        key = (member.member_type, rounded)
        buckets[key].append(member.label)

    # Build MemberGroup for each bucket
    groups: list[MemberGroup] = []
    for (mtype, unit_len), labels in buckets.items():
        count = len(labels)
        groups.append(MemberGroup(
            member_type=mtype,
            unit_length=unit_len,
            count=count,
            total_length=unit_len * count,
            member_labels=labels,
        ))

    # Sort: by type order, then by length descending
    groups.sort(key=lambda g: (_TYPE_ORDER.get(g.member_type, 99), -g.unit_length))

    # Totals
    total_members = sum(g.count for g in groups)
    total_length = sum(g.total_length for g in groups)

    return QuantityTakeoff(
        groups=groups,
        total_members=total_members,
        total_length=total_length,
        group_tolerance=tolerance,
    )


def _round_to(value: float, tolerance: float) -> float:
    """Round value to the nearest multiple of tolerance."""
    if tolerance <= 0:
        return round(value, 1)
    return round(value / tolerance) * tolerance
