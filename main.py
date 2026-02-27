import json
import os
import re
import traceback

import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from drawing import analyze_drawing
from drawing.steel_sections import build_fix_r15_catalog

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.0-flash")
gemini_y2_model = genai.GenerativeModel("gemini-3-pro-preview")

app = FastAPI()
templates = Jinja2Templates(directory="templates")


class ChatMessage(BaseModel):
    message: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/chat")
async def chat(msg: ChatMessage):
    response = model.generate_content(msg.message)
    return {"reply": response.text}


@app.get("/api/member-catalog")
def member_catalog():
    """Return the FIX-R-15 member catalog (section → unit weight)."""
    catalog = build_fix_r15_catalog()
    return catalog.model_dump()


@app.post("/api/analyze")
def analyze(file: UploadFile = File(...)):
    """Accept a PDF upload, run the drawing analysis pipeline, return JSON."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return {"error": "Invalid file type", "detail": "Please upload a PDF file."}

    try:
        pdf_bytes = file.file.read()
        result = analyze_drawing(pdf_bytes, file.filename)
        return result.model_dump()
    except Exception as e:
        return {
            "error": "Analysis failed",
            "detail": str(e),
            "traceback": traceback.format_exc(),
        }


class GeminiAxialRequest(BaseModel):
    image: str  # base64 PNG
    view_name: str  # e.g. "Y2通り", "X1通り", "Xn+1通り", "X2~Xn通り"
    span: float | None = None  # mm (Y direction)
    length: float | None = None  # mm (X direction)


@app.post("/api/gemini-analyze-axial")
async def gemini_analyze_axial(req: GeminiAxialRequest):
    """Send axial frame drawing image to Gemini and detect members with total lengths."""
    try:
        prompt = f"""This is a Japanese steel building elevation drawing: 軸組図 ({req.view_name}).

Find all member labels (circled numbers like ①, ②, ③) in the image.
Some labels have modifiers like "内側" or "外側" next to them — treat these as separate members.

CRITICAL RULES:
- You MUST calculate each member's length by reading the actual dimension numbers shown in the image (寸法線の数値). Do NOT guess or assume lengths — use only the values printed on the drawing.
- If dimension lines show sub-segments (e.g. 7500 + 7500), SUM them to get the full span between grid lines (= 15000). Never use only a partial segment when the member spans the full distance between two grids.
- Every labeled member visible in the drawing has a physical length. Never return 0 or null for unit_length_mm — find the correct dimension value from the image.

For each label:
1. Count how many structural lines it points to (via leader lines). This is the "line_count" (本数).
2. Determine orientation: "x" (horizontal in the drawing), "y" (vertical in the drawing), "diagonal", or "arch".
3. Calculate **unit_length_mm** (length of ONE member) by reading the dimension numbers from the image:
   - For horizontal members: identify which grid lines the member spans (e.g. Y1 to Y2). Read ALL dimension segments between those grids and SUM them. Example: if dimensions show 7500 + 7500 between Y1 and Y2, then unit_length_mm = 15000.
   - For vertical members: read the vertical dimension values that the member spans. Look at the dimension lines on the side of the drawing to find the value that matches the member's actual extent. A member may NOT span the full height — use only the dimensions that correspond to the actual member length.
   - For the main frame truss (typically ①): this is ALWAYS a CONTINUOUS element from foundation to foundation. Even if other labeled members exist on the columns, the main frame truss MUST include: left column (vertical portion) + arch/curved roof + right column (vertical portion). Sum ALL segments. The column height is shown as a vertical dimension (e.g. 4900), and the arch length should be estimated from the span and rise dimensions. Example: if column height = 4900 and arch length ≈ 15860, then unit_length_mm = 4900 + 15860 + 4900 = 25660.
   - For curved/arch portions: estimate arc length from span and rise. Arc length ≈ span × (1 + (2/3) × (rise/span)²) is a reasonable approximation.
4. Calculate **total_length_mm** = unit_length_mm × line_count.

Return ONLY JSON:
{{
  "members": [
    {{
      "member_number": "1",
      "modifier": "",
      "label": "①",
      "description": "brief description of what this member is and which dimension values were used",
      "line_count": 0,
      "orientation": "x",
      "unit_length_mm": null,
      "total_length_mm": null
    }}
  ]
}}"""

        image_size_kb = len(req.image) * 3 / 4 / 1024  # base64 to raw bytes in KB
        print(f"[Gemini] {req.view_name} image size: {image_size_kb:.1f} KB")

        image_part = {"mime_type": "image/png", "data": req.image}
        response = gemini_y2_model.generate_content([prompt, image_part])

        raw_text = response.text.strip()
        # Extract JSON from markdown code block if present
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_text)
        if json_match:
            raw_text = json_match.group(1).strip()

        result = json.loads(raw_text)
        members = result.get("members", [])

        # Ensure total_length is consistent with unit_length × line_count
        for m in members:
            line_count = m.get("line_count", 0) or 0
            unit_length = m.get("unit_length_mm")
            if unit_length and line_count:
                m["total_length_mm"] = line_count * unit_length

        return {"members": members, "raw_response": response.text}

    except json.JSONDecodeError:
        return {
            "error": "Gemini応答のJSON解析に失敗しました",
            "raw_response": response.text if 'response' in dir() else "",
        }
    except Exception as e:
        return {
            "error": "Gemini解析に失敗しました",
            "detail": str(e),
            "traceback": traceback.format_exc(),
        }
