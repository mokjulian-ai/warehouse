import os
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
