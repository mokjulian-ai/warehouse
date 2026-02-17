"""Main analysis pipeline orchestrator. Runs steps A through G."""

import base64

import fitz

from .dimensions import extract_dimensions
from .grids import extract_grid_system
from .heights import extract_heights
from .koyafuse import detect_koyafuse_members
from .matching import run_matching
from .quantity import compute_quantity_takeoff
from .reconstruction import reconstruct_3d
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

    # Render all pages as PNG images
    page_images: list[str] = []
    for pi in range(page_count):
        px = doc[pi].get_pixmap(dpi=150)
        page_images.append(base64.b64encode(px.tobytes("png")).decode("ascii"))

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

    # Step 3: 3D Structural Reconstruction
    structural_model = None
    if matching:
        structural_model = reconstruct_3d(matching, grid)
        if structural_model:
            diagnostics["structural_member_count"] = len(structural_model.members)
            diagnostics["structural_frame_count"] = structural_model.frame_count
            diagnostics["structural_member_summary"] = structural_model.member_summary

    # Step 4: Quantity Takeoff
    quantity_takeoff = None
    if structural_model:
        quantity_takeoff = compute_quantity_takeoff(structural_model)
        if quantity_takeoff:
            diagnostics["quantity_groups"] = len(quantity_takeoff.groups)
            diagnostics["quantity_total_members"] = quantity_takeoff.total_members
            diagnostics["quantity_total_length_m"] = round(quantity_takeoff.total_length / 1000, 1)

    # Step 5: 小屋伏図 member detection
    koyafuse = detect_koyafuse_members(doc)
    if koyafuse:
        diagnostics["koyafuse_page"] = koyafuse.page_index
        diagnostics["koyafuse_members"] = len(koyafuse.detected_members)

        # Compute member lengths using matching dimensions
        if matching:
            for m in koyafuse.detected_members:
                if m.orientation == "x" and matching.length:
                    m.unit_length = matching.length
                elif m.orientation == "y" and matching.span:
                    m.unit_length = matching.span
                if m.unit_length and m.tip_count:
                    m.total_length = m.tip_count * m.unit_length

    doc.close()

    # Step G: Assemble result
    return AnalysisResult(
        filename=filename,
        page_count=page_count,
        page_width=primitives.page_width,
        page_height=primitives.page_height,
        page_rotation=page_rotation,
        page_image=page_image,
        page_images=page_images,
        views=views,
        grid_system=grid,
        dimensions=dimensions,
        heights=heights,
        quality=quality,
        matching=matching,
        structural_model=structural_model,
        quantity_takeoff=quantity_takeoff,
        koyafuse=koyafuse,
        diagnostics=diagnostics,
    )
