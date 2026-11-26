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


COURSE_CODE = re.compile(r"\bcourse\s+code\s*[:|]?\s*([A-Z]{2,}\s*\d{2,}[A-Z0-9]*)\b", re.I)


def course_range(pages, code):
    starts = [(index, re.sub(r"\s+", "", match[1]).upper()) for index, page in enumerate(pages)
              if (match := COURSE_CODE.search(page[:1800]))]
    target = re.sub(r"\s+", "", code).upper()
    for index, (start, found) in enumerate(starts):
        if found == target:
            end = starts[index + 1][0] if index + 1 < len(starts) else len(pages)
            return range(start, end)
    return None


def extract(name, raw, *, syllabus_code=None):
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
            if doc.needs_pass:
                raise ValueError(f"PDF must be unlocked and contain at most {limit} pages")
            selected = range(len(doc))
            if len(doc) > limit:
                if not syllabus_code or len(doc) > 500:
                    raise ValueError(f"PDF must contain at most {limit} pages")
                selected = course_range([page.get_text()[:1800] for page in doc], syllabus_code)
                if selected is None or len(selected) > limit:
                    raise ValueError(f"Could not isolate {syllabus_code} in this PDF. Upload the pages for that course.")
            pages = []
            scanned = 0
            for index in selected:
                page = doc[index]
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
            return [{"number": index + 1, "text": text.strip(), "ocr_confidence": confidence,
                     "review": confidence is not None and confidence < 70}
                    for index, (text, confidence) in zip(selected, pages)]
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


HEADING = re.compile(r"^\s*#{0,6}\s*(?:module|unit)\s*[-–:]?\s*([IVX]+|\d+)\b\s*[:.\-–|]?\s*(.*)", re.I)
SECTION = re.compile(r"^\s*#{0,6}\s*(course outcomes?|text\s*books?|reference books?|references?|(?:course )?assessment|examination|exam pattern|evaluation)\b\s*[:\-]?\s*(.*)", re.I)
ASSESSMENT_SUM = re.compile(r"\b(\d+)\s*[x×]\s*(\d+)\s*=\s*(\d+)\s*marks?\b", re.I)


def pdf_modules(raw, pages):
    modules = {}
    last = None
    with pymupdf.open(stream=raw, filetype="pdf") as document:
        for page in pages:
            visible = " ".join(page["text"].lower().split())
            starts_table = "syllabus description" in visible or ("module" in visible and "contact hours" in visible)
            if not starts_table and last is None:
                continue
            found = False
            try:
                tables = document[page["number"] - 1].find_tables().tables
            except (ValueError, RuntimeError):
                tables = []
            for table in tables:
                rows = table.extract()
                if not rows or len(rows[0]) != 3:
                    continue
                first, description, hours = (" ".join((value or "").lower().split()) for value in rows[0])
                header = "module" in first and ("syllabus" in description or "description" in description) and "hour" in hours
                if not header and (last is None or starts_table):
                    continue
                for number, description, hours in rows[1 if header else 0:]:
                    number, description, hours = ((value or "").strip() for value in (number, description, hours))
                    if not number and not hours and last is not None and len(description) >= 12:
                        last["content"] += "\n" + description
                        found = True
                    elif re.fullmatch(r"[1-9]\d?", number) and len(description) >= 12 and re.fullmatch(r"\d{1,2}(?:\.\d+)?", hours):
                        last = modules.setdefault(number, {"id": number, "hours": float(hours), "content": description})
                        found = True
                if found:
                    break
            if not found or "course assessment method" in visible:
                last = None
    for module in modules.values():
        first, _, rest = module.pop("content").partition("\n")
        title = first.strip(" :-–")
        has_title = bool(rest and len(title) <= 60 and not re.search(r"[,;]$", title))
        content = rest if has_title else first + "\n" + rest
        topics = re.split(r"\s*;\s*|\s*\.\s+(?=[A-Z])", " ".join(content.split()))
        module.update(name=title if has_title else f"Module {module['id']}",
                      topics=list(dict.fromkeys(topic.strip(" .") for topic in topics if topic.strip(" ."))),
                      outcomes=[])
    return list(modules.values())


def parse_syllabus(pages, *, raw=None, course_code=""):
    result = {"modules": [], "outcomes": [], "textbooks": [], "pattern": default_pattern(), "pattern_evidence": []}
    if course_code and (selected := course_range([page["text"] for page in pages], course_code)) is not None:
        pages = [pages[index] for index in selected]
    elif course_code and sum(bool(COURSE_CODE.search(page["text"][:1800])) for page in pages) > 1:
        return result
    source = "\n".join(page["text"] for page in pages)
    module = None
    section = None
    for source_line in source.splitlines():
        line = source_line.strip()
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
            section = "outcomes" if title.lower().startswith("course outcome") else "textbooks" if title[0].lower() in "tr" else "pattern"
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

    modules = {module["id"]: module for module in result["modules"]}
    if raw and raw.startswith(b"%PDF-") and (table_modules := pdf_modules(raw, pages)):
        corrected_ids = {heading[1].upper() for page in pages if page.get("corrected")
                         for line in page["text"].splitlines() if (heading := HEADING.match(line))}
        modules = {module["id"]: modules[module["id"]] if module["id"] in corrected_ids else module
                   for module in table_modules} | {key: modules[key] for key in corrected_ids if key in modules}
    result["modules"] = list(modules.values())[:20]
    for module in result["modules"]:
        module["topics"] = list(dict.fromkeys(module["topics"]))
    evidence = " ".join(result["pattern_evidence"])
    pattern = result["pattern"]
    for field, expression in {"cie": r"\bCIE\s*(?:marks)?\s*[:=]?\s*(\d+)",
                              "ese": r"\bESE\s*(?:marks)?\s*[:=]?\s*(\d+)",
                              "duration_minutes": r"\b(\d+)\s*minutes\b"}.items():
        if match := re.search(expression, evidence, re.I):
            pattern[field] = int(match[1])
    if match := re.search(r"\bExam(?:ination)?\s+(?:Hours|Duration)\s*[:|]?\s*(\d+)\s*H(?:ou)?rs?\.?\s*(?:(\d+)\s*Min\.?)?", source, re.I):
        pattern["duration_minutes"] = 60 * int(match[1]) + int(match[2] or 0)
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
    elif result["modules"]:
        # KTU tables often show the attempted-question mark calculations under each part.
        calculations = list(dict.fromkeys(tuple(map(int, match.groups())) for match in ASSESSMENT_SUM.finditer(source)))
        count = len(result["modules"])
        if (len(calculations) == 2 and sum(total for _, _, total in calculations) == pattern["ese"]
                and all(questions % count == 0 and questions * marks == total for questions, marks, total in calculations)
                and re.search(r"out of (?:two|2) questions", " ".join(source.lower().split()))):
            # Part A attempts all questions. Part B offers a choice of two per module.
            a, b = sorted(calculations, key=lambda values: values[0], reverse=True)
            if a[0] != b[0] and b[0] == count:
                result["pattern_evidence"].extend(match[0] for match in ASSESSMENT_SUM.finditer(source)
                                                  if match[0] not in result["pattern_evidence"])
                for part, (questions, marks, _) in zip(pattern["parts"], (a, b)):
                    part.update(attempt=questions // count, offered=questions // count if part["name"] == "A" else 2,
                                marks=marks)
                if match := re.search(r"maximum of\s+(\d+)\s+sub\s*divisions?", " ".join(source.split()), re.I):
                    pattern["parts"][1]["subdivisions"] = int(match[1])
    return result
