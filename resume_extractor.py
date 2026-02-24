"""
resume_extractor.py
Extracts structured candidate data from a resume image or PDF using OpenAI vision.
"""

import base64
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from openai import OpenAI

client = OpenAI()  # Uses OPENAI_API_KEY from environment


EXTRACTION_PROMPT = """
You are a resume data-extraction assistant. Analyze the resume image(s) provided and return ONLY a single valid JSON object — no markdown, no commentary, no extra text.

Use EXACTLY these field names and rules:

{
  "First Name": "",
  "Last Name": "",
  "Email": "",
  "Phone": "",
  "City": "",
  "State": "",
  "College / University": "",
  "Expected Graduation Year": "",
  "Degree": "",
  "Major": "",
  "Company 1": "",
  "Title 1": "",
  "Dates 1": "",
  "Company 2": "",
  "Title 2": "",
  "Dates 2": "",
  "Company 3": "",
  "Title 3": "",
  "Dates 3": "",
  "Leadership & Organizations": "",
  "Skills": ""
}

RULES:
- First Name / Last Name: Split full name; ignore middle names.
- Email: Extract email address. If missing, use "Not Provided".
- Phone: Extract phone number. If missing, leave empty.
- City / State: Extract from address or header. If missing, leave empty.
- College / University: Full name, no abbreviations unless written that way.
- Expected Graduation Year: Convert "Class of 2026" → "2026". Use most recent degree. If already graduated, still include the year.
- Degree: e.g. "Bachelor of Science", "MBA", "Associate of Arts".
- Major: Field of study.
- Company 1/2/3 + Title 1/2/3 + Dates 1/2/3: Up to 3 most recent or most professional roles. Prefer professional over internships. Date format: "Jan 2023 – May 2024".
- Leadership & Organizations: Combine all clubs, boards, student orgs, volunteer work. Comma-separated string.
- Skills: Clean comma-separated list. Remove filler language.
- If a field has no data, return empty string "".
- Do NOT hallucinate any data.
- Return ONLY the JSON object.
"""


def image_to_base64(path: str) -> str:
    """Convert an image file to base64 string."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def pdf_to_images(pdf_path: str) -> list[str]:
    """Convert PDF pages to image files, return list of image paths."""
    from pdf2image import convert_from_path

    tmp_dir = tempfile.mkdtemp()
    images = convert_from_path(pdf_path, dpi=200, fmt="jpeg")
    paths = []
    for i, img in enumerate(images):
        img_path = os.path.join(tmp_dir, f"page_{i+1}.jpg")
        img.save(img_path, "JPEG")
        paths.append(img_path)
    return paths


def extract_resume_data(file_path: str) -> dict:
    """
    Main entry point. Accepts a PDF or image path.
    Returns a fully populated dict with all Google Sheet fields + ConstantContact mapping.
    """
    ext = Path(file_path).suffix.lower()

    # Prepare image(s) for vision API
    if ext == ".pdf":
        image_paths = pdf_to_images(file_path)
    elif ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        image_paths = [file_path]
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    # Build content array for OpenAI vision
    content = [{"type": "text", "text": EXTRACTION_PROMPT}]
    for img_path in image_paths[:4]:  # max 4 pages
        b64 = image_to_base64(img_path)
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64}",
                "detail": "high"
            }
        })

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": content}],
        max_tokens=2000,
        temperature=0
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    extracted = json.loads(raw)

    # Ensure required fields
    for req in ["First Name", "Last Name", "Email"]:
        if not extracted.get(req):
            extracted[req] = "Not Provided"

    # Add Date Added
    extracted["Date Added"] = date.today().strftime("%Y-%m-%d")

    # Placeholders (filled later by other modules)
    extracted["Resume Image URL"] = ""
    extracted["Drive File ID"] = ""

    # Derive Classification
    classification = _derive_classification(extracted)

    # Build full output
    result = {
        "GoogleSheet": extracted,
        "ConstantContact": {
            "ContactFields": {
                "Email": extracted.get("Email", ""),
                "FirstName": extracted.get("First Name", ""),
                "LastName": extracted.get("Last Name", ""),
                "Phone": extracted.get("Phone", ""),
                "City": extracted.get("City", ""),
                "State": extracted.get("State", "")
            },
            "CustomFields": {
                "School": extracted.get("College / University", ""),
                "Major": extracted.get("Major", ""),
                "Classification": classification
            }
        }
    }

    return result


def _derive_classification(data: dict) -> str:
    """Determine Student / Graduate / Professional based on graduation year."""
    grad_year_str = data.get("Expected Graduation Year", "").strip()
    current_year = date.today().year

    if grad_year_str:
        # Extract 4-digit year
        match = re.search(r"\b(20\d{2}|19\d{2})\b", grad_year_str)
        if match:
            grad_year = int(match.group(1))
            if grad_year > current_year:
                return "Student"
            else:
                return "Graduate"

    # No graduation year — check for professional experience
    has_experience = any(
        data.get(f"Company {i}", "").strip()
        for i in range(1, 4)
    )
    if has_experience:
        return "Professional"

    return "Not Provided"
