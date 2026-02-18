"""Main analysis pipeline orchestrator. Runs steps A through G."""

import base64

import fitz

from .dimensions import extract_dimensions
from .grids import extract_grid_system
from .heights import extract_heights
from .axial_frame import detect_axial_frame_members
from .koyafuse import detect_koyafuse_members
from .matching import run_matching
from .steel_sections import build_fix_r15_catalog
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

    # Shared catalog lookup for weight assignment (Steps 5, 5b)
    from .steel_sections import MemberEntry
    catalog_map: dict[str, MemberEntry] = {}

    # Step 5: 小屋伏図 member detection
    koyafuse = detect_koyafuse_members(doc)
    if koyafuse:
        diagnostics["koyafuse_page"] = koyafuse.page_index
        diagnostics["koyafuse_members"] = len(koyafuse.detected_members)

        # Render cropped image of just the drawing area
        if koyafuse.drawing_bbox:
            bb = koyafuse.drawing_bbox
            pad = 15  # padding in points
            clip = fitz.Rect(
                bb["x0"] - pad, bb["y0"] - pad,
                bb["x1"] + pad, bb["y1"] + pad,
            )
            kf_page = doc[koyafuse.page_index]
            pix = kf_page.get_pixmap(dpi=150, clip=clip)
            koyafuse.drawing_image = base64.b64encode(
                pix.tobytes("png")
            ).decode("ascii")

        # Compute member lengths using matching dimensions
        if matching:
            for m in koyafuse.detected_members:
                if m.orientation == "x" and matching.length:
                    m.unit_length = matching.length
                elif m.orientation == "y" and matching.span:
                    m.unit_length = matching.span
                if m.unit_length and m.line_count:
                    m.total_length = m.line_count * m.unit_length

        # Assign weights from member catalog
        if not catalog_map:
            catalog = build_fix_r15_catalog()
            for entry in catalog.entries:
                num_str = entry.number
                base_num = ""
                for ch in num_str:
                    cp = ord(ch)
                    if 0x2460 <= cp <= 0x2473:
                        base_num = str(cp - 0x2460 + 1)
                    elif 0x2474 <= cp <= 0x2487:
                        base_num = str(cp - 0x2474 + 1)
                if base_num and base_num not in catalog_map:
                    catalog_map[base_num] = entry

        for m in koyafuse.detected_members:
            entry = catalog_map.get(m.member_number)
            if entry:
                m.section_text = entry.section_text
                if entry.truss:
                    m.chord_weight_per_m = entry.truss.chord_weight_per_m
                    m.lattice_weight_per_m = entry.truss.lattice_weight_per_m
                    # Lattice truss members in 小屋伏図 → use lattice weight
                    m.member_kind = "lattice"
                    m.unit_weight = entry.truss.lattice_weight_per_m
                else:
                    # Simple member — use full unit_weight
                    m.unit_weight = entry.unit_weight
                if m.total_length and m.unit_weight:
                    m.total_weight = m.total_length / 1000 * m.unit_weight

    # Step 5b: 軸組図 Y1 member detection
    axial_frame = detect_axial_frame_members(doc)
    if axial_frame:
        diagnostics["axial_frame_page"] = axial_frame.page_index
        diagnostics["axial_frame_members"] = len(axial_frame.detected_members)

        # Render cropped image of drawing area
        if axial_frame.drawing_bbox:
            bb = axial_frame.drawing_bbox
            pad = 15
            clip = fitz.Rect(
                bb["x0"] - pad, bb["y0"] - pad,
                bb["x1"] + pad, bb["y1"] + pad,
            )
            af_page = doc[axial_frame.page_index]
            pix = af_page.get_pixmap(dpi=150, clip=clip)
            axial_frame.drawing_image = base64.b64encode(
                pix.tobytes("png")
            ).decode("ascii")

        # Compute member lengths from matching dimensions
        if matching:
            for m in axial_frame.detected_members:
                if m.orientation == "x" and matching.length:
                    m.unit_length = matching.length
                elif m.orientation == "y" and matching.span:
                    m.unit_length = matching.span
                if m.unit_length and m.line_count:
                    m.total_length = m.line_count * m.unit_length

        # Assign weights from member catalog (reuse catalog if already built)
        if not catalog_map:
            catalog = build_fix_r15_catalog()
            for entry in catalog.entries:
                num_str = entry.number
                base_num = ""
                for ch in num_str:
                    cp = ord(ch)
                    if 0x2460 <= cp <= 0x2473:
                        base_num = str(cp - 0x2460 + 1)
                if base_num and base_num not in catalog_map:
                    catalog_map[base_num] = entry

        for m in axial_frame.detected_members:
            entry = catalog_map.get(m.member_number)
            if entry:
                m.section_text = entry.section_text
                if entry.truss:
                    m.chord_weight_per_m = entry.truss.chord_weight_per_m
                    m.lattice_weight_per_m = entry.truss.lattice_weight_per_m
                    m.member_kind = "lattice"
                    m.unit_weight = entry.truss.lattice_weight_per_m
                else:
                    m.unit_weight = entry.unit_weight
                if m.total_length and m.unit_weight:
                    m.total_weight = m.total_length / 1000 * m.unit_weight

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
        axial_frame=axial_frame,
        diagnostics=diagnostics,
    )
