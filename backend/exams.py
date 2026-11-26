import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .chat import source_block
from .core import get, put
from .db import Subject, Upload
from .main import DB, require, upload_data
from .providers import ProviderError, Unavailable, router
from .research import search
from .retrieval import map_topic, query, words

api = APIRouter(prefix="/api")
WEIGHTS = json.loads(Path(__file__).with_name("prediction_weights.json").read_text(encoding="utf-8"))
QUESTION = re.compile(r"^\s*(?:Q(?:uestion)?\s*\.?\s*)?(\d{1,2})(?:[.)]|\s+(?=[A-Z]))\s*(.*)", re.I)
MARKS = re.compile(r"(?:\((\d+)\s*(?:marks?)?\)|\[(\d+)\s*(?:marks?)?\]|(\d+)\s*marks?)\s*$", re.I)


class PaperInput(BaseModel):
    year: int | None = Field(default=None, ge=1980, le=2100)
    session: str = Field(default="", max_length=80)
    scheme: int | None = Field(default=None, ge=1980, le=2100)
    approved: bool = True


class WebPaper(PaperInput):
    url: str = Field(max_length=2048)
    approved: Literal[True]


def paper_fields(text):
    year = re.search(r"\b(?:exam(?:ination)?\s+year|year)\s*[:=-]?\s*(20\d{2})\b", text, re.I)
    session = re.search(r"\b((?:january|february|march|april|may|june|july|august|september|october|november|december)(?:\s*/\s*[a-z]+)?)\s+(20\d{2})\b", text, re.I)
    scheme = re.search(r"\b(?:(20\d{2})\s+scheme|scheme\s*[:=-]?\s*(20\d{2}))\b", text, re.I)
    return {"year": int(year[1] if year else session[2]) if year or session else None,
            "session": session[1] if session else "", "scheme": int(scheme[1] or scheme[2]) if scheme else None}


def extract_questions(upload, outline, *, include_unmatched=False):
    metadata = paper_fields("\n".join(page["text"] for page in upload.pages))
    metadata.update({key: value for key, value in upload.metadata_json.items() if key in metadata and value})
    entries = []
    current = None
    part = None
    for page in upload.pages:
        for line in page["text"].splitlines():
            if match := re.match(r"^\s*PART\s+([A-Z])\b", line, re.I):
                part = match[1].upper()
                current = None
                continue
            if match := QUESTION.match(line):
                current = {"number": int(match[1]), "text": match[2].strip(), "part": part, "page": page["number"]}
                entries.append(current)
            elif current and line.strip() and not re.match(r"^\s*(Module|Page)\s+\d+", line, re.I):
                current["text"] += " " + line.strip()
    output = []
    for row in entries:
        mark = MARKS.search(row["text"])
        if mark:
            row["marks"] = int(next(group for group in mark.groups() if group))
            row["text"] = row["text"][:mark.start()].strip()
        else:
            row["marks"] = None
        if len(row["text"]) < 10:
            continue
        module, topic = map_topic(row["text"], outline)
        if topic and metadata["scheme"] != 2024:
            normalized = words(topic)
            if not normalized or len(normalized & words(row["text"])) / len(normalized) < .65:
                module, topic = None, None
        if not topic and not include_unmatched:
            continue
        if row["part"] is None and row["marks"]:
            matching = [part["name"] for part in outline["pattern"]["parts"] if part["marks"] == row["marks"]]
            if len(matching) == 1:
                row["part"] = matching[0]
        output.append(dict(row, **metadata, module=module, topic=topic, upload_id=upload.id,
                           name=upload.name, url=upload.metadata_json.get("url") or f"/api/uploads/{upload.id}/pages/{row['page']}"))
    return output


def cluster_questions(questions):
    groups = []
    for question in questions:
        terms = words(question["text"])
        for group in groups:
            exemplar = group[0]
            if exemplar["topic"] != question["topic"] or exemplar["part"] != question["part"]:
                continue
            other = words(exemplar["text"])
            if len(terms & other) / max(1, len(terms | other)) >= .52:
                group.append(question)
                break
        else:
            groups.append([question])
    return groups


def score_topics(outline, questions, year=None):
    year = year or date.today().year
    pattern = outline["pattern"]
    grouped = defaultdict(list)
    for question in questions:
        grouped[(question["module"], question["topic"])].append(question)
    paper_count = max(1, len({question["upload_id"] for question in questions}))
    max_hours = max([1, *(module["hours"] for module in outline["modules"])])
    max_outcomes = max([1, *(len(module["outcomes"]) for module in outline["modules"])])
    result = []
    for module in outline["modules"]:
        for topic in module["topics"]:
            evidence = grouped[(module["id"], topic)]
            support = {}
            for question in evidence:
                weight = 1 if question["scheme"] == 2024 else WEIGHTS["older_scheme"]
                recency = .25 if not question["year"] else 2 ** (-max(0, year - question["year"]) / WEIGHTS["half_life_years"])
                support[question["upload_id"]] = (weight, weight * recency)
            factors = {
                "frequency": sum(weight for weight, _ in support.values()) / paper_count,
                "recency": sum(recency for _, recency in support.values()) / paper_count,
                "module_weight": sum(part["attempt"] * part["marks"] /
                                     (max(1, len(outline["modules"])) if part.get("scope") == "paper" else 1)
                                     for part in pattern["parts"]) / pattern["ese"],
                "hours": module["hours"] / max_hours,
                "outcomes": len(module["outcomes"]) / max_outcomes,
            }
            score = sum(WEIGHTS[name] * value for name, value in factors.items())
            priority = "High" if score >= WEIGHTS["high"] else "Medium" if score >= WEIGHTS["medium"] else "Low"
            result.append({"module": module["id"], "topic": topic, "priority": priority, "score": round(score, 4),
                           "factors": {key: round(val, 4) for key, val in factors.items()},
                           "evidence": evidence, "clusters": cluster_questions(evidence)})
    return sorted(result, key=lambda row: -row["score"])


def backtest(outline, questions):
    papers = {(row["year"], row["session"].lower(), row["upload_id"]) for row in questions if row["year"]}
    if len(papers) < 2:
        return None
    year, session, latest = max(papers)
    if len({paper[1] for paper in papers if paper[0] == year}) > 1:
        months = {name.lower(): index for index, name in enumerate(
            ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
        year, session, latest = max(papers, key=lambda paper: (paper[0], months.get(paper[1].split("/")[0].strip(), 0), paper[2]))
    training = [row for row in questions if row["upload_id"] != latest]
    hidden = {(row["module"], row["topic"]) for row in questions if row["upload_id"] == latest}
    top = set()
    ranking = score_topics(outline, training, year)
    for module in outline["modules"]:
        ranked = [row for row in ranking if row["module"] == module["id"]]
        slots = sum(part["offered"] for part in outline["pattern"]["parts"] if part.get("scope") != "paper")
        top.update((row["module"], row["topic"]) for row in ranked[:slots])
    overall_slots = sum(part["offered"] for part in outline["pattern"]["parts"] if part.get("scope") == "paper")
    top.update((row["module"], row["topic"]) for row in ranking[:overall_slots])
    hits = len(hidden & top)
    return {"year": year, "session": session, "paper": latest, "hits": hits, "total": len(hidden),
            "rate": round(hits / len(hidden), 3) if hidden else 0, "method": "Topic recall in the offered-question slots"}


def predictions(db, subject):
    papers = db.scalars(select(Upload).where(Upload.subject_id == subject.id, Upload.tag == "pyq")).all()
    questions = [row for paper in papers if paper.metadata_json.get("approved", True)
                 for row in extract_questions(paper, subject.outline)]
    ranked = score_topics(subject.outline, questions)
    count = len({row["upload_id"] for row in questions if row["scheme"] == 2024})
    return {"topics": ranked, "weights": WEIGHTS, "backtest": backtest(subject.outline, questions),
            "confidence": "Low: fewer than three matched 2024-scheme papers" if count < 3 else "Historical evidence available",
            "matched_questions": len(questions), "scheme_papers": count}


def generate_paper(subject, ranked):
    outline = subject.outline if hasattr(subject, "outline") else subject
    pattern = outline["pattern"]
    variants = (
        "Explain {topic} with a worked example.",
        "Describe how {topic} works, including an important limitation.",
        "Compare two approaches related to {topic} and justify when to use each.",
        "Apply {topic} to a realistic case and interpret the result.",
    )
    sections = []
    number = 0
    for part in pattern["parts"]:
        groups = []
        in_paper = part.get("scope") == "paper"
        modules = [{"id": "all", "name": "All modules"}] if in_paper else outline["modules"]
        for module in modules:
            candidates = [(row["module"], row["topic"]) for row in ranked if in_paper or row["module"] == module["id"]]
            if not candidates:
                candidates = [(item["id"], topic) for item in (outline["modules"] if in_paper else [module])
                              for topic in item["topics"] or [item["name"]]]
            if not candidates:
                continue
            questions = []
            for index in range(part["offered"]):
                chosen_module, topic = candidates[index % len(candidates)]
                variant = index + len(sections)
                number += 1
                questions.append({"id": str(number), "text": variants[variant % len(variants)].format(topic=topic),
                                  "marks": part["marks"], "topic": topic, "module": chosen_module, "generated": True})
            groups.append({"module": module["id"], "name": module["name"], "attempt": part["attempt"], "questions": questions})
        sections.append({"name": part["name"], "marks": part["marks"], "max_subdivisions": part["subdivisions"], "groups": groups})
    return {"sections": sections, "total": sum(group["attempt"] * section["marks"] for section in sections for group in section["groups"]),
            "minutes": pattern["duration_minutes"]}


@api.get("/subjects/{subject_id}/predictions")
def prediction_view(subject_id: int, db: DB):
    return predictions(db, require(db, Subject, subject_id))


@api.put("/subjects/{subject_id}/papers/{upload_id}")
def paper_metadata(subject_id: int, upload_id: int, body: PaperInput, db: DB):
    paper = require(db, Upload, upload_id)
    if paper.subject_id != subject_id or paper.tag != "pyq":
        raise HTTPException(404, "Paper does not exist for this subject")
    paper.metadata_json = {**paper.metadata_json, **body.model_dump()}
    db.commit()
    return upload_data(paper)


@api.get("/subjects/{subject_id}/papers/{upload_id}/questions")
def paper_questions(subject_id: int, upload_id: int, db: DB):
    subject = require(db, Subject, subject_id)
    paper = require(db, Upload, upload_id)
    if paper.subject_id != subject_id or paper.tag != "pyq":
        raise HTTPException(404, "Paper does not exist for this subject")
    return {"metadata": {**paper_fields("\n".join(page["text"] for page in paper.pages)), **paper.metadata_json},
            "questions": extract_questions(paper, subject.outline, include_unmatched=True)}


@api.post("/subjects/{subject_id}/paper-search")
async def paper_search(subject_id: int, db: DB):
    subject = require(db, Subject, subject_id)
    found = await search.search(f'KTU {subject.code} {subject.name} 2024 scheme previous question paper model paper official syllabus filetype:pdf')
    put(db, "paper_candidates", subject_id, {"results": found})
    db.commit()
    return found


@api.post("/subjects/{subject_id}/papers/approve")
async def approve_paper(subject_id: int, body: WebPaper, db: DB):
    import httpx
    from .documents import MAX_BYTES, extract
    from .research import AGENT, permitted
    from .retrieval import index_upload

    subject = require(db, Subject, subject_id)
    found = (get(db, "paper_candidates", subject_id) or {}).get("results", [])
    candidate = next((item for item in found if item["url"] == body.url), None)
    if not candidate:
        raise HTTPException(404, "Run paper search and choose a result first")
    existing = db.scalars(select(Upload).where(Upload.subject_id == subject.id, Upload.tag == "pyq")).all()
    if any(item.metadata_json.get("url") == body.url for item in existing):
        raise HTTPException(409, "This paper is already in your library")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": AGENT}) as client:
            if not await permitted(client, body.url):
                raise HTTPException(422, "This source cannot be downloaded under its robots rules")
            data = bytearray()
            async with client.stream("GET", body.url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise HTTPException(422, "Paper exceeds 20 MB")
        name = candidate["name"][:220] + ".pdf"
        from starlette.concurrency import run_in_threadpool
        pages = await run_in_threadpool(extract, name, bytes(data))
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        raise HTTPException(422, "The source did not provide a readable public PDF") from exc
    paper = Upload(subject_id=subject.id, name=name, tag="pyq", pages=pages, metadata_json=body.model_dump())
    db.add(paper)
    db.flush()
    index_upload(db, paper, subject)
    db.commit()
    return upload_data(paper)


@api.get("/subjects/{subject_id}/paper")
def mock_paper(subject_id: int, db: DB):
    subject = require(db, Subject, subject_id)
    if not subject.outline["modules"]:
        raise HTTPException(422, "Confirm the syllabus before generating a paper")
    return generate_paper(subject, predictions(db, subject)["topics"])


class ModelAnswer(BaseModel):
    module: str
    topic: str
    question: str = Field(min_length=1, max_length=1200)
    marks: int = Field(ge=1, le=100)


@api.post("/subjects/{subject_id}/model-answer")
async def answer_model(subject_id: int, body: ModelAnswer, db: DB):
    from .chat import SYSTEM, mock_answer, sanitize_citations
    from .study import demo_content, study_scope
    subject = require(db, Subject, subject_id)
    study_scope(subject, body.module, body.topic)
    sources = query(db, body.topic, subject_id, body.module, body.topic)
    user = f"Write a {body.marks}-mark model answer to: {body.question}\nUntrusted sources:\n{source_block(sources)}"
    demo = demo_content(subject, body.topic)

    def fixture():
        if not demo:
            return mock_answer(sources)
        reference = next((item for item in sources if item["name"] == "sample-reference.txt"), None)
        label = f" [{reference['label']}]" if reference else ""
        paragraphs = [f"**Definition.** {demo[0]}{label}"]
        if body.marks > 3:
            paragraphs.append(f"**Worked example.** {demo[1]}{label}")
        paragraphs.append(f"**Cost or limitation.** {demo[4]}{label}")
        return "\n\n".join(paragraphs) + "\n\n*Bundled demo answer.*"

    try:
        text, info = await router.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                           fixture=fixture)
    except (Unavailable, ProviderError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"text": sanitize_citations(text, sources), "sources": sources, **info}
