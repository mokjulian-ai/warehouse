"""Main analysis pipeline orchestrator. Runs steps A through G."""

import base64

import fitz

from .dimensions import extract_dimensions
from .grids import extract_grid_system
from .heights import extract_heights
from .matching import run_matching
from .models import AnalysisResult, GateStatus, QualityCheck, QualityReport
from .primitives import extract_page_primitives
from .quality import run_quality_gates
from .views import segment_views


def analyze_drawing(pdf_bytes: bytes, filename: str) -> AnalysisResult:
    """
    Full analysis pipeline.

    Args:
        pdf_bytes: Raw PDF file content.
        filename: Original filename for the result.

    Returns:
        AnalysisResult with all extracted data and quality report.
    """
    diagnostics: dict = {}

    # Open PDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    page = doc[0]  # Engineering drawings typically single-page

    # Render page to PNG image for PDF viewer tab
    pix = page.get_pixmap(dpi=150)
    page_image = base64.b64encode(pix.tobytes("png")).decode("ascii")
    page_rotation = page.rotation

    # Step A: Extract primitives
    primitives = extract_page_primitives(page)
    diagnostics["text_count"] = len(primitives.texts)
    diagnostics["line_count"] = len(primitives.lines)
    diagnostics["rect_count"] = len(primitives.rects)

    # Step B: Segment views
    views = segment_views(primitives, page_rotation=page_rotation)
    diagnostics["views_found"] = [v.view_type.value for v in views]

    # Step C: Extract grid system
    grid = extract_grid_system(views, page_rotation=page_rotation)
    if grid:
        diagnostics["grid_x_labels"] = [l.label for l in grid.x_labels]
        diagnostics["grid_y_labels"] = [l.label for l in grid.y_labels]

    # Step D: Extract dimensions
    dimensions = extract_dimensions(views)
    diagnostics["dimension_count"] = len(dimensions)
    diagnostics["dimension_values"] = [d.value for d in dimensions[:20]]

    # Step E: Extract heights
    heights = extract_heights(views)
    diagnostics["heights"] = [
        {"type": h.height_type.value, "value": h.value}
        for h in heights
    ]

    # Step F: Quality gates
    quality = run_quality_gates(views, grid, dimensions, heights)

    # Step 2: Cross-view matching
    matching = run_matching(views, grid, dimensions, heights, page_rotation=page_rotation)
    if matching:
        diagnostics["matching_span"] = matching.span
        diagnostics["matching_length"] = matching.length
        diagnostics["matching_pitch"] = matching.bay_pitch
        diagnostics["matching_bay_count"] = matching.bay_count
        diagnostics["matching_eave_height"] = matching.eave_height
        diagnostics["matching_max_height"] = matching.max_height

    doc.close()

    # Step G: Assemble result
    return AnalysisResult(
        filename=filename,
        page_count=page_count,
        page_width=primitives.page_width,
        page_height=primitives.page_height,
        page_rotation=page_rotation,
        page_image=page_image,
        views=views,
        grid_system=grid,
        dimensions=dimensions,
        heights=heights,
        quality=quality,
        matching=matching,
        diagnostics=diagnostics,
    )
