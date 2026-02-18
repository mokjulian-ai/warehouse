"""All Pydantic data models for the drawing analysis pipeline."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# --- Primitives (Step A) ---


class Point(BaseModel):
    x: float
    y: float


class BBox(BaseModel):
    """Bounding box: (x0, y0) top-left, (x1, y1) bottom-right."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def center(self) -> Point:
        return Point(x=(self.x0 + self.x1) / 2, y=(self.y0 + self.y1) / 2)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    def contains(self, p: Point) -> bool:
        return self.x0 <= p.x <= self.x1 and self.y0 <= p.y <= self.y1

    def overlaps(self, other: BBox) -> bool:
        return not (
            self.x1 < other.x0
            or other.x1 < self.x0
            or self.y1 < other.y0
            or other.y1 < self.y0
        )

    def expand(self, margin: float) -> BBox:
        return BBox(
            x0=self.x0 - margin,
            y0=self.y0 - margin,
            x1=self.x1 + margin,
            y1=self.y1 + margin,
        )

    def intersection(self, other: BBox) -> BBox | None:
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        if x0 < x1 and y0 < y1:
            return BBox(x0=x0, y0=y0, x1=x1, y1=y1)
        return None


class TextSpan(BaseModel):
    """A single text element extracted from the PDF."""

    text: str
    bbox: BBox
    center: Point
    font: str = ""
    size: float = 0.0


class Line(BaseModel):
    """A vector line segment."""

    p1: Point
    p2: Point
    length: float
    angle: float  # degrees, 0=right, 90=down
    width: float = 1.0
    color: list[float] | None = None


class PagePrimitives(BaseModel):
    """All raw primitives extracted from one PDF page."""

    page_index: int
    page_width: float
    page_height: float
    texts: list[TextSpan]
    lines: list[Line]
    rects: list[BBox]


# --- Views (Step B) ---


class ViewType(str, Enum):
    ROOF_PLAN = "屋根伏図"
    FLOOR_PLAN = "平面図"
    ELEVATION = "立面図"
    SECTION = "断面図"
    UNKNOWN = "unknown"


class View(BaseModel):
    view_type: ViewType
    title_text: str
    title_bbox: BBox
    region: BBox
    scale: str | None = None
    texts: list[TextSpan]
    lines: list[Line]


# --- Grid (Step C) ---


class GridAxis(str, Enum):
    X = "X"
    Y = "Y"


class GridLabel(BaseModel):
    axis: GridAxis
    label: str
    index: int
    position: float
    text_span: TextSpan
    line: Line | None = None


class GridSystem(BaseModel):
    x_labels: list[GridLabel]
    y_labels: list[GridLabel]
    source_view: ViewType


# --- Dimensions (Step D) ---


class DimensionType(str, Enum):
    SINGLE = "single"
    PITCH = "pitch"
    REPEAT = "repeat"


class Dimension(BaseModel):
    value: float
    raw_text: str
    dim_type: DimensionType
    repeat_count: int | None = None
    text_span: TextSpan
    nearest_lines: list[Line] = Field(default_factory=list)
    source_view: ViewType | None = None


# --- Heights (Step E) ---


class HeightType(str, Enum):
    EAVE_HEIGHT = "軒高"
    MAX_HEIGHT = "最高高さ"
    GL = "GL"
    FL = "FL"
    DESIGN_GL = "設計GL"


class HeightParam(BaseModel):
    height_type: HeightType
    value: float | None = None
    raw_text: str
    text_span: TextSpan
    source_view: ViewType | None = None


# --- Quality (Step F) ---


class GateStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class QualityCheck(BaseModel):
    name: str
    status: GateStatus
    message: str
    detail: str | None = None


class QualityReport(BaseModel):
    overall: GateStatus
    checks: list[QualityCheck]


# --- Step 2: Cross-View Matching ---


class ViewGridInfo(BaseModel):
    """Grid labels detected in a specific view."""

    view_index: int
    view_type: ViewType
    view_title: str
    grid_side: str | None = None  # For elevations: "Y1", "Y2", etc.
    x_labels: list[str] = Field(default_factory=list)
    y_labels: list[str] = Field(default_factory=list)


class FrameLink(BaseModel):
    """Cross-view link for one X-grid position."""

    x_label: str
    plan_x_position: float | None = None
    in_elevation_sides: list[str] = Field(default_factory=list)


class AnchoredParam(BaseModel):
    """A building parameter anchored to grid positions."""

    name: str
    value: float
    unit: str = "mm"
    anchor_from: str | None = None
    anchor_to: str | None = None
    source_view: ViewType | None = None
    raw_text: str = ""
    computed: bool = False  # True if computed from grid, not from dimension text


class MatchingResult(BaseModel):
    """Step 2 output: cross-view matching results."""

    canonical_grid_source: ViewType
    view_grid_info: list[ViewGridInfo]
    frame_links: list[FrameLink]
    anchored_params: list[AnchoredParam]
    consistency_checks: list[QualityCheck]
    span: float | None = None
    length: float | None = None
    bay_pitch: float | None = None
    bay_count: int | None = None
    eave_height: float | None = None
    max_height: float | None = None


# --- Step 3: 3D Structural Reconstruction ---


class MemberType(str, Enum):
    COLUMN = "column"
    RAFTER = "rafter"
    RIDGE_BEAM = "ridge_beam"
    PURLIN = "purlin"


class Point3D(BaseModel):
    """A point in the 3D building coordinate system (mm)."""

    x: float  # Longitudinal (building length direction)
    y: float  # Transverse (span direction)
    z: float  # Vertical (height)


class Member3D(BaseModel):
    """A structural member defined by two 3D endpoints."""

    member_type: MemberType
    label: str = ""
    start: Point3D
    end: Point3D
    length: float  # True 3D length in mm
    frame_index: int | None = None  # None for purlins/ridge beam


class BuildingEnvelope(BaseModel):
    """Bounding dimensions of the building."""

    length: float
    span: float
    eave_height: float
    ridge_height: float


class StructuralModel(BaseModel):
    """Step 3 output: 3D structural wireframe model."""

    members: list[Member3D]
    envelope: BuildingEnvelope
    frame_count: int
    bay_count: int
    bay_pitch: float
    x_grid_positions: list[float]
    y_grid_positions: list[float]
    member_summary: dict[str, int] = Field(default_factory=dict)


# --- Step 4: Quantity Takeoff ---


class MemberGroup(BaseModel):
    """A group of identical members (same type + similar length)."""

    member_type: MemberType
    unit_length: float  # Representative length in mm
    count: int
    total_length: float  # mm
    section: str | None = None  # e.g. "H-200x100x5.5x8" (future)
    unit_weight: float | None = None  # kg/m (future)
    total_weight: float | None = None  # kg (future)
    member_labels: list[str] = Field(default_factory=list)


class QuantityTakeoff(BaseModel):
    """Step 4 output: grouped member quantities."""

    groups: list[MemberGroup]
    total_members: int
    total_length: float  # mm
    total_weight: float | None = None  # kg (None until section data available)
    group_tolerance: float = 10.0  # mm tolerance for length grouping


# --- Koyafuse (小屋伏図) Member Detection ---


class LeaderTip(BaseModel):
    """A single arrow tip of a leader line."""

    x: float
    y: float
    length: float  # Distance from leader origin to this tip


class DetectedMember(BaseModel):
    """A member detected from the 小屋伏図 via leader line tracing."""

    member_number: str  # e.g. "1", "2", "7"
    modifier: str = ""  # e.g. "内側", "外側"
    label: str = ""  # Combined: "2内側"
    label_x: float  # Visual X position of label
    label_y: float  # Visual Y position of label
    leader_tips: list[LeaderTip] = Field(default_factory=list)
    tip_count: int = 0
    line_count: int = 0  # Total structural lines (including unlabeled ones)
    line_positions: list[list[float]] = Field(default_factory=list)  # [[x, y], ...] visual coords of all structural lines
    orientation: str = ""  # "x" (longitudinal) or "y" (transverse)
    unit_length: float | None = None  # mm per member
    total_length: float | None = None  # mm (line_count * unit_length)
    section_text: str = ""  # From catalog: e.g. "2Ps-42.7φ×2.3t, D=450, ..."
    member_kind: str = ""  # "chord", "lattice", or "" (simple member)
    unit_weight: float | None = None  # kg/m actually used for this member
    chord_weight_per_m: float | None = None  # kg/m (chord only, for reference)
    lattice_weight_per_m: float | None = None  # kg/m (lattice only, for reference)
    total_weight: float | None = None  # kg (total_length/1000 * unit_weight)


class KoyafuseResult(BaseModel):
    """Result of 小屋伏図 member detection."""

    page_index: int
    scale: str = ""  # e.g. "1/100"
    detected_members: list[DetectedMember]
    drawing_bbox: dict | None = None  # Visual bounding box of drawing area
    drawing_image: str = ""  # base64 PNG of cropped drawing area
    mediabox_width: float = 0.0  # For coordinate inverse transform
    page_visual_width: float = 0.0  # page.rect width (after rotation)
    page_visual_height: float = 0.0  # page.rect height (after rotation)


class AxialFrameResult(BaseModel):
    """Result of 軸組図 member detection (used for Y1, Y2, X1, Xn+1, X2~Xn)."""

    page_index: int
    scale: str = ""
    detected_members: list[DetectedMember]
    drawing_bbox: dict | None = None
    drawing_image: str = ""
    mediabox_width: float = 0.0
    page_visual_width: float = 0.0
    page_visual_height: float = 0.0


# --- Final Output (Step G) ---


class AnalysisResult(BaseModel):
    filename: str
    page_count: int
    page_width: float
    page_height: float
    page_rotation: int = 0
    page_image: str | None = None  # base64 PNG of rendered page
    page_images: list[str] = Field(default_factory=list)  # base64 PNGs of all pages
    views: list[View]
    grid_system: GridSystem | None = None
    dimensions: list[Dimension]
    heights: list[HeightParam]
    quality: QualityReport
    matching: MatchingResult | None = None
    structural_model: StructuralModel | None = None
    quantity_takeoff: QuantityTakeoff | None = None
    koyafuse: KoyafuseResult | None = None
    axial_frame: AxialFrameResult | None = None
    axial_frame_y2: AxialFrameResult | None = None
    axial_frame_x1: AxialFrameResult | None = None
    axial_frame_xn1: AxialFrameResult | None = None
    axial_frame_x2xn: AxialFrameResult | None = None
    diagnostics: dict = Field(default_factory=dict)
