import io
import os
import re
import zipfile
from pathlib import Path
from typing import Literal

import pymupdf
import pytesseract
from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from pptx import Presentation
from pydantic import BaseModel, Field, model_validator

from .db import HOSTED

MAX_BYTES = 20 * 1024 * 1024
FORMATS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]


class Part(BaseModel):
    name: str
    scope: Literal["module", "paper"] = "module"
    offered: int = Field(ge=1, le=20)
    attempt: int = Field(ge=1, le=20)
    marks: int = Field(ge=1, le=100)
    subdivisions: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def attempts_fit(self):
        if self.attempt > self.offered:
            raise ValueError("Attempted questions cannot exceed offered questions")
        return self


class Pattern(BaseModel):
    cie: int = Field(default=40, ge=0, le=200)
    ese: int = Field(default=60, ge=1, le=300)
    duration_minutes: int = Field(default=150, ge=1, le=600)
    parts: list[Part] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_parts(self):
        if len({part.name for part in self.parts}) != len(self.parts):
            raise ValueError("Exam part names must be unique")
        return self


class Module(BaseModel):
    id: str = Field(min_length=1, max_length=30)
    name: str
    hours: float = Field(default=0, ge=0)
    topics: list[str]
    outcomes: list[str] = []


class Outline(BaseModel):
    modules: list[Module] = Field(max_length=20)
    outcomes: list[str] = []
    textbooks: list[str] = []
    pattern: Pattern
    pattern_evidence: list[str] = []

    @model_validator(mode="after")
    def validate_outline(self):
        if len({m.id for m in self.modules}) != len(self.modules):
            raise ValueError("Module IDs must be unique")
        if any(len(set(module.topics)) != len(module.topics) for module in self.modules):
            raise ValueError("Topics within a module must be unique")
        total = sum(part.attempt * part.marks * (len(self.modules) if part.scope == "module" else 1)
                    for part in self.pattern.parts)
        if self.modules and total != self.pattern.ese:
            raise ValueError(f"The question pattern awards {total} marks, but the ESE is {self.pattern.ese}")
        return self


def default_pattern():
    return Pattern(parts=[Part(name="A", offered=2, attempt=2, marks=3),
                          Part(name="B", offered=2, attempt=1, marks=9, subdivisions=3)]).model_dump()


def ocr(image):
    try:
        raw = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, timeout=8 if HOSTED else 25)
    except (pytesseract.TesseractError, pytesseract.TesseractNotFoundError, RuntimeError):
        return "", 0
    lines = {}
    for index, word in enumerate(raw["text"]):
        if word.strip():
            location = tuple(raw[key][index] for key in ("block_num", "par_num", "line_num"))
            lines.setdefault(location, []).append((word, float(raw["conf"][index])))
    scores = [score for line in lines.values() for _, score in line if score >= 0]
    return "\n".join(" ".join(word for word, _ in line) for line in lines.values()), round(sum(scores) / len(scores)) if scores else 0


def extract(name, raw):
    suffix = Path(name).suffix.lower()
    if suffix not in FORMATS or not raw or len(raw) > MAX_BYTES:
        raise ValueError("Choose a supported, nonempty file up to 20 MB")
    if suffix in {".docx", ".pptx"}:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if "[Content_Types].xml" not in archive.namelist() or sum(f.file_size for f in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("Invalid or oversized Office document")
    if suffix == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("The file is not a PDF")
        with pymupdf.open(stream=raw, filetype="pdf") as doc:
            limit = 120 if HOSTED else 300
            if doc.needs_pass or len(doc) > limit:
                raise ValueError(f"PDF must be unlocked and contain at most {limit} pages")
            pages = []
            scanned = 0
            for page in doc:
                native = page.get_text(sort=True).strip()
                page_area = max(1, page.rect.width * page.rect.height)
                image_cover = max((pymupdf.Rect(image["bbox"]).get_area() / page_area
                                   for image in page.get_image_info()), default=0)
                if len(native) >= 40 and not (image_cover > .6 and len(native) < 350):
                    pages.append((native, None))
                elif HOSTED and scanned >= 8:
                    pages.append((native, 0))
                else:
                    scanned += 1
                    max_pixels = 3_000_000 if os.getenv("RENDER") == "true" else 12_000_000
                    scale = min(1.6, (max_pixels / page_area) ** .5)
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB)
                    text, confidence = ocr(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                    pages.append((text if len(text) > len(native) else native, confidence))
    elif suffix == ".docx":
        doc = Document(io.BytesIO(raw))
        pages = [("", None)]
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        for child in doc.element.body:
            if child.tag.endswith("}p"):
                paragraph = Paragraph(child, doc)
                pieces = ["\f"] if paragraph.paragraph_format.page_break_before and pages[-1][0].strip() else []
                contents = []
                for item in child.iter():
                    if item.tag == qn("w:t"):
                        contents.append(item.text or "")
                    elif item.tag == qn("w:tab"):
                        contents.append("\t")
                    elif item.tag == qn("w:lastRenderedPageBreak"):
                        if not contents or contents[-1] != "\f":
                            contents.append("\f")
                    elif item.tag == qn("w:br"):
                        contents.append("\f" if item.get(qn("w:type")) == "page" else "\n")
                pieces.extend(re.split(r"(\f)", "".join(contents)))
            elif child.tag.endswith("}tbl"):
                pieces = ["\n".join(" | ".join(c.text for c in row.cells) for row in Table(child, doc).rows)]
            else:
                continue
            for piece in pieces:
                if piece == "\f":
                    pages.append(("", None))
                else:
                    pages[-1] = (pages[-1][0] + piece + "\n", None)
    elif suffix == ".pptx":
        deck = Presentation(io.BytesIO(raw))
        pages = []
        for slide in deck.slides:
            lines = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    lines.append(shape.text)
                if shape.has_table:
                    lines.extend(" | ".join(c.text for c in row.cells) for row in shape.table.rows)
            pages.append(("\n".join(lines), None))
    elif suffix in {".txt", ".md", ".markdown"}:
        raw_text = raw.decode("utf-8-sig")
        if "\0" in raw_text:
            raise ValueError("Text files must contain UTF-8 text")
        pages = [(part, None) for part in raw_text.split("\f")]
    else:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 32_000_000:
                raise ValueError("Image exceeds 32 megapixels")
            pages = [ocr(image)]
    return [{"number": i, "text": text.strip(), "ocr_confidence": conf,
             "review": conf is not None and conf < 70} for i, (text, conf) in enumerate(pages, 1)]


HEADING = re.compile(r"^\s*#{0,6}\s*(?:module|unit)\s+([IVX]+|\d+)\b\s*[:.\-–]?\s*(.*)", re.I)
SECTION = re.compile(r"^\s*#{0,6}\s*(course outcomes?|textbooks?|references?|assessment|examination|exam pattern|evaluation)\b\s*[:\-]?\s*(.*)", re.I)


def parse_syllabus(pages):
    result = {"modules": [], "outcomes": [], "textbooks": [], "pattern": default_pattern(), "pattern_evidence": []}
    module = None
    section = None
    for raw in "\n".join(page["text"] for page in pages).splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = HEADING.match(line)
        if heading:
            number, name = heading.groups()
            hours = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", name, re.I)
            clean_name = re.sub(r"\(?\b\d+(?:\.\d+)?\s*(?:hours?|hrs?)\b\)?", "", name, flags=re.I).strip(" -–()")
            module = {"id": number.upper(), "name": clean_name or f"Module {number}", "hours": float(hours[1]) if hours else 0,
                      "topics": [], "outcomes": []}
            result["modules"].append(module)
            section = "module"
            continue
        heading = SECTION.match(line)
        if heading:
            title, line = heading.groups()
            section = "outcomes" if title.lower().startswith("course") else "textbooks" if title[0].lower() in "tr" else "pattern"
        if not line:
            continue
        if section in {"outcomes", "textbooks"}:
            result[section].append(line.lstrip("-* "))
        elif section == "module" and module:
            hours = re.fullmatch(r"[-*\s]*(?:hours?:\s*)?(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)?", line, re.I)
            if hours:
                module["hours"] = float(hours[1])
            elif re.match(r"^CO\s*\d", line, re.I):
                module["outcomes"].extend(re.findall(r"CO\s*\d+", line, re.I))
            elif not re.search(r"\b(CIE|ESE|Part [A-Z])\b", line, re.I):
                module["topics"].extend(piece.strip(" -*") for piece in line.split(";") if piece.strip(" -*"))
        if section == "pattern" or re.search(r"\b(CIE|ESE|Part [AB])\b", line, re.I):
            result["pattern_evidence"].append(line)

    evidence = " ".join(result["pattern_evidence"])
    pattern = result["pattern"]
    for field, expression in {"cie": r"\bCIE\s*(?:marks)?\s*[:=]?\s*(\d+)",
                              "ese": r"\bESE\s*(?:marks)?\s*[:=]?\s*(\d+)",
                              "duration_minutes": r"\b(\d+)\s*minutes\b"}.items():
        if match := re.search(expression, evidence, re.I):
            pattern[field] = int(match[1])
    parts = []
    number_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

    def number(token):
        return number_words[token.lower()] if token.lower() in number_words else int(token)

    for line in result["pattern_evidence"]:
        if match := re.search(r"\bPart\s+([A-Z])\s*[:\-]?\s*(\d+)\s*(?:questions?\s*)?[x×]\s*(\d+)", line, re.I):
            name, count, marks = match.groups()
            previous = next((part for part in pattern["parts"] if part["name"] == name.upper()), None)
            per_module = re.search(r"\b(\d+|one|two|three|four|five)\s+(?:questions?\s+)?per module", line, re.I)
            scope = "paper" if re.search(r"\b(?:whole paper|across all modules|overall)\b", line, re.I) else "module"
            if scope == "paper":
                offered = int(count)
            elif per_module and not re.search(r"marks?\s+per module", line, re.I):
                offered = number(per_module[1])
            elif re.search(r"[x×]\s*\d+\s*(?:marks?\s*)?per module", line, re.I):
                offered = int(count)
            else:
                offered = max(1, int(count) // max(1, len(result["modules"])))
            choice = re.search(r"\b(?:answer|attempt)\s+(\d+|one|two|three|four|five|all)\b", line, re.I)
            if choice:
                attempt = offered if choice[1].lower() == "all" else number(choice[1])
            elif scope == "paper" or re.search(r"\ball compulsory\b", line, re.I):
                attempt = offered
            else:
                attempt = min(offered, previous["attempt"] if previous else offered)
            offered = max(offered, attempt)
            subdivisions = re.search(r"\b(\d+)\s+subdivisions?", line, re.I)
            parts.append({"name": name.upper(), "scope": scope, "offered": offered, "attempt": attempt, "marks": int(marks),
                          "subdivisions": int(subdivisions[1]) if subdivisions else previous["subdivisions"] if previous else 1})
    if parts:
        pattern["parts"] = parts
    return result
