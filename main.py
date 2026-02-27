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

For each label:
- Count how many structural lines it points to (via leader lines)
- Determine orientation: "x" (horizontal), "y" (vertical), "diagonal", or "arch"
- Calculate total length using the dimension numbers shown in the drawing.
  IMPORTANT: A member may be a continuous element — e.g. a main frame truss that includes columns + arch as one piece. Trace the FULL path of the member (columns + curved/straight portions) and sum all segments. Do not count only the arch or only the columns.
  For curved/arch portions, estimate the arc length from the span and rise dimensions.

Return ONLY JSON:
{{
  "members": [
    {{
      "member_number": "1",
      "modifier": "",
      "label": "①",
      "description": "brief description",
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

        # Recalculate total_length on server side for accuracy
        for m in members:
            orientation = m.get("orientation", "")
            line_count = m.get("line_count", 0) or 0
            if orientation == "x" and req.length:
                m["unit_length_mm"] = req.length
                m["total_length_mm"] = line_count * req.length
            elif orientation == "y" and req.span:
                m["unit_length_mm"] = req.span
                m["total_length_mm"] = line_count * req.span
            elif m.get("unit_length_mm") and line_count:
                m["total_length_mm"] = line_count * m["unit_length_mm"]

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
