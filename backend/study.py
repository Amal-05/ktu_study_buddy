import asyncio
import html
import io
import json
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

import pymupdf
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from sample.reference import TOPICS

from .chat import SYSTEM, Scope, sanitize_citations, scope_values, source_block
from .core import get, put, records
from .db import Record, Session, Subject
from .main import DB, require
from .providers import MOCK, ProviderError, Unavailable, router
from .research import search
from .retrieval import query

api = APIRouter(prefix="/api")
SECTIONS = ["Definition", "Intuition", "Detailed explanation", "Worked example", "Diagram", "Formulas",
            "Tables", "Common mistakes", "3-mark answer", "9-mark answer", "Quick revision summary"]


class StudyInput(BaseModel):
    module: str = Field(min_length=1)
    topic: str | None = None


class NoteEdit(StudyInput):
    text: str = Field(min_length=1, max_length=100000)
    version: str


class NoteBody(BaseModel):
    sections: dict[str, str]


class LessonBody(BaseModel):
    explanation: str
    example: str
    question: str
    expected_answer: str


class LessonInput(StudyInput):
    action: str = Field(default="start", max_length=80)
    answer: str = Field(default="", max_length=8000)


class Evaluation(BaseModel):
    correct: bool
    feedback: str


def study_scope(subject, module, topic=None):
    found = next((item for item in subject.outline["modules"] if item["id"] == module), None)
    if found is None or topic is not None and topic not in found["topics"]:
        raise HTTPException(422, "Select a saved module and topic")
    return found


def study_key(subject, module, topic):
    return json.dumps([subject, module, topic], ensure_ascii=False)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def source_info(sources):
    return [{key: value for key, value in source.items() if key != "text"} for source in sources]


def demo_content(subject, topic):
    return TOPICS.get(topic) if subject.code == "DEMO201" else None


def note_fixture(subject, topic, sources):
    demo = demo_content(subject, topic)
    syllabus = next((source for source in sources if source["tag"] == "syllabus"), None)
    label = f" [{syllabus['label']}] Scope only." if syllabus else ""
    if not demo:
        valid = next((source for source in sources if source["tag"] != "syllabus"), None)
        return {"sections": {section: (f"[{valid['label']}] {valid['text'][:500]}" if valid and section == "Definition" else
                                       "Mock mode has no authored content for this topic." + label) for section in SECTIONS}}
    definition, example, question, answers, detail = demo
    origin = "\n\n*Bundled demo explanation.*" + label
    values = [definition, f"Think about this example: {example}", detail, example,
              "```mermaid\nflowchart LR\n  A[Read the input] --> B[Apply the rule] --> C[Check the result]\n```",
              "See the cost or condition in the detailed explanation.",
              f"| Check | Expected result |\n| --- | --- |\n| {question} | {answers[0]} |",
              "State the assumptions and check the result using a small example.",
              f"{definition} {detail}", f"{definition}\n\n{example}\n\n{detail}",
              f"{definition}\n\n**Recall:** {question}"]
    return {"sections": {section: value + origin for section, value in zip(SECTIONS, values)}}


async def build_note(db, subject, module, topic):
    sources = query(db, topic, subject.id, module, topic, tags={"textbook"}, limit=3)
    sources += query(db, topic, subject.id, module, topic, tags={"syllabus", "notes"}, limit=4)
    if not any(s["tag"] in {"textbook", "notes"} for s in sources):
        sources += (await search.search(f"{subject.name} {topic} explanation examples"))[:3]
    sources = [dict(item, label=f"S{i}") for i, item in enumerate(sources, 1)]
    prompt = (f"Generate complete learning notes for '{topic}' in {subject.name}. Prefer uploaded prescribed textbooks and "
              f"cite each section, or mark it General model knowledge. Use Mermaid, LaTeX, tables and worked steps where applicable. "
              f"Return JSON with a 'sections' object keyed by these exact headings: {json.dumps(SECTIONS)}.\n"
              f"Untrusted sources:\n{source_block(sources)}")
    raw, info = await router.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                                      schema=NoteBody, fixture=lambda: note_fixture(subject, topic, sources))
    sections = NoteBody.model_validate_json(raw).sections
    body = [f"# {topic}"]
    for heading in SECTIONS:
        val = sanitize_citations(sections.get(heading, "Section missing from provider response."), sources)
        if not re.search(r"\[S\d+\]", val) and not MOCK and "general model knowledge" not in val.lower():
            val += "\n\n*General model knowledge.*"
        body.append(f"## {heading}\n\n{val}")
    return {"id": uuid4().hex, "text": "\n\n".join(body), "created": stamp(), "sources": source_info(sources),
            "edited": False, "provider": info["provider"], "estimated_tokens": info["estimated_tokens"]}


def note_data(db, subject_id, module, topic):
    return get(db, "notes", study_key(subject_id, module, topic), {"versions": [], "active": None})


@api.get("/subjects/{subject_id}/notes")
def notes(subject_id: int, module: str, topic: str, db: DB):
    study_scope(require(db, Subject, subject_id), module, topic)
    return note_data(db, subject_id, module, topic)


@api.put("/subjects/{subject_id}/notes")
def edit_note(subject_id: int, body: NoteEdit, db: DB):
    study_scope(require(db, Subject, subject_id), body.module, body.topic)
    value = note_data(db, subject_id, body.module, body.topic)
    selected = next((item for item in value["versions"] if item["id"] == body.version), None)
    if selected is None:
        raise HTTPException(404, "Note revision does not exist")
    updated = {**selected, "id": uuid4().hex, "text": body.text, "edited": True, "created": stamp()}
    value["versions"].insert(0, updated)
    value["active"] = updated["id"]
    put(db, "notes", study_key(subject_id, body.module, body.topic), value)
    db.commit()
    return value


@api.get("/subjects/{subject_id}/notes/export")
def export_note(subject_id: int, module: str, topic: str, version: str, format: str, db: DB):
    study_scope(require(db, Subject, subject_id), module, topic)
    value = note_data(db, subject_id, module, topic)
    selected = next((item for item in value["versions"] if item["id"] == version), None)
    if selected is None:
        raise HTTPException(404, "Note revision does not exist")
    sources = "\n".join(f"- [{item['label']}] {item['name']}" + (f", page {item['page']}" if "page" in item else "")
                        + f" ({item['url']})" for item in selected["sources"])
    body = selected["text"] + ("\n\n## Sources\n\n" + sources if sources else "")
    if format == "md":
        return Response(body, media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="study-notes.md"'})
    if format != "pdf":
        raise HTTPException(422, "Choose PDF or Markdown")
    lines = []
    pre = False
    for line in body.splitlines():
        if line.startswith("```"):
            lines.append("</pre>" if pre else "<pre>")
            pre = not pre
        elif pre:
            lines.append(html.escape(line) + "\n")
        elif match := re.match(r"^(#{1,6}) (.*)", line):
            level = len(match[1])
            lines.append(f"<h{level}>{html.escape(match[2])}</h{level}>")
        elif line:
            lines.append(f"<p>{html.escape(line)}</p>")
    if pre:
        lines.append("</pre>")
    story = pymupdf.Story(html="".join(lines), user_css="body {font-size: 11pt; line-height:1.35;} pre {white-space:pre-wrap;}")
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    page = pymupdf.paper_rect("a4")
    content = page + (40, 40, -40, -40)
    story.write(writer, lambda *_: (page, content, None))
    writer.close()
    return Response(buf.getvalue(), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="study-notes.pdf"'})


@api.post("/subjects/{subject_id}/notes/jobs", status_code=202)
def enqueue_notes(subject_id: int, body: StudyInput, db: DB):
    module = study_scope(require(db, Subject, subject_id), body.module, body.topic)
    topics = [body.topic] if body.topic else module["topics"]
    if not topics:
        raise HTTPException(422, "Add topics to this module")
    existing = next((row for _, row in records(db, "job") if row["subject_id"] == subject_id
                     and row["module"] == body.module and row["topics"] == topics
                     and row["state"] in {"queued", "running", "waiting"}), None)
    if existing:
        return existing
    job = {"id": uuid4().hex, "subject_id": subject_id, "module": body.module, "topics": topics, "completed": 0,
           "state": "queued", "message": "", "retry_at": 0}
    put(db, "job", job["id"], job)
    db.commit()
    return job


@api.get("/jobs/{key}")
def job_status(key: str, db: DB):
    result = get(db, "job", key)
    if result is None:
        raise HTTPException(404, "Job does not exist")
    return result


@api.post("/jobs/{key}/retry")
def job_retry(key: str, db: DB):
    job = job_status(key, db)
    if job["state"] not in {"waiting", "failed"}:
        raise HTTPException(409, "Only a failed or waiting job can be retried")
    job.update(state="queued", retry_at=0, message="")
    put(db, "job", key, job)
    db.commit()
    return job


async def run_jobs():
    with Session() as db:
        for key, job in records(db, "job"):
            if job["state"] == "running":
                put(db, "job", key, {**job, "state": "queued"})
        db.commit()
    while True:
        with Session() as db:
            jobs = records(db, "job")
            pending = next((job for _, job in reversed(jobs) if job["state"] in {"queued", "waiting"} and job["retry_at"] <= time.time()), None)
            if pending:
                pending["state"] = "running"
                put(db, "job", pending["id"], pending)
                db.commit()
        if not pending:
            await asyncio.sleep(.8)
            continue
        try:
            subject_id, module = pending["subject_id"], pending["module"]
            for topic in pending["topics"][pending["completed"]:]:
                with Session() as db:
                    subject = require(db, Subject, subject_id)
                    study_scope(subject, module, topic)
                    note = await build_note(db, subject, module, topic)
                with Session() as db:
                    value = note_data(db, subject_id, module, topic)
                    value["versions"].insert(0, note)
                    current = next((item for item in value["versions"] if item["id"] == value["active"]), None)
                    if current is None or not current["edited"]:
                        value["active"] = note["id"]
                    put(db, "notes", study_key(subject_id, module, topic), value)
                    pending["completed"] += 1
                    put(db, "job", pending["id"], pending)
                    db.commit()
            pending.update(state="done", message="", retry_at=0)
        except Unavailable as exc:
            pending.update(state="waiting" if exc.next_reset else "failed", message=str(exc), retry_at=exc.next_reset or 0)
        except ProviderError as exc:
            pending.update(state="failed", message=str(exc))
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Note job failed")
            pending.update(state="failed", message="Could not generate the notes. Check the source and try again.")
        with Session() as db:
            put(db, "job", pending["id"], pending)
            db.commit()


def lesson_public(value):
    return {key: item for key, item in value.items() if key != "expected_answer"}


@api.get("/subjects/{subject_id}/lesson")
def lesson_resume(subject_id: int, module: str, topic: str, db: DB):
    study_scope(require(db, Subject, subject_id), module, topic)
    return lesson_public(get(db, "lesson", study_key(subject_id, module, topic), {}))


@api.post("/subjects/{subject_id}/lesson")
async def lesson(subject_id: int, body: LessonInput, db: DB):
    subject = require(db, Subject, subject_id)
    study_scope(subject, body.module, body.topic)
    if not body.topic:
        raise HTTPException(422, "Select a topic")
    key = study_key(subject_id, body.module, body.topic)
    previous = get(db, "lesson", key, {})
    demo = demo_content(subject, body.topic)
    sources = query(db, body.topic, subject_id, body.module, body.topic)
    if previous and body.action == "start":
        return lesson_public(previous)
    try:
        if body.action == "answer":
            if not previous or not body.answer.strip():
                raise HTTPException(422, "Start a step and enter an answer")
            accepted = (demo[3] if demo and previous["step"] % 2 == 1 and previous.get("action") != "quiz me"
                        else [previous["expected_answer"]])
            normalize = lambda text: " ".join(re.findall(r"\w+", text.casefold()))
            normalized = normalize(body.answer)
            correct = any(normalized == normalize(text) for text in accepted)
            fixture = {"correct": correct, "feedback": "Correct. You can continue to the next step." if correct else
                       f"Review the example and try again. Expected idea: {previous['expected_answer']}"}
            prompt = f"Evaluate the student's answer. Return JSON correct and feedback. Question: {previous['question']}\nExpected: {previous['expected_answer']}\nStudent answer: {body.answer}"
            raw, _ = await router.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                                           schema=Evaluation, fixture=fixture)
            previous["evaluation"] = Evaluation.model_validate_json(raw).model_dump()
            previous["answer"] = body.answer
            result = previous
            from .practice import update_mastery
            update_mastery(db, subject_id, body.module, body.topic, float(result["evaluation"]["correct"]))
        else:
            step = previous.get("step", 1) + (1 if body.action == "continue" and previous else 0)
            fixture = {"explanation": "Mock mode has no scripted lesson for this topic.", "example": "",
                       "question": "What is this topic about?", "expected_answer": body.topic}
            if demo:
                definition, example, question, answers, detail = demo
                explanation = definition if step % 2 else detail
                expected = answers[0] if step % 2 else detail
                question = question if step % 2 else f"State the key cost or operating condition for {body.topic}."
                if body.action == "simpler":
                    explanation = f"Start with a small input. {definition}"
                elif body.action == "more detail":
                    explanation = f"{definition}\n\n{detail}"
                elif body.action == "another example":
                    example = f"Change one input in this example and trace the same rule: {example}"
                elif body.action == "Malayalam-English":
                    explanation = f"ആദ്യം example നോക്കാം. {definition} ഇനി step by step നോക്കാം."
                elif body.action == "quiz me":
                    question, expected = demo[2], demo[3][0]
                fixture = {"explanation": explanation + " *Bundled demo lesson.*", "example": example,
                           "question": question, "expected_answer": expected}
            prompt = (f"Teach {body.topic}, step {step}. Student action: {body.action}. Previous step: {previous.get('explanation', '')}. "
                      "Give a concise granular explanation, a worked example (numeric or code line by line where relevant), "
                      "one question and expected_answer. Return these fields as JSON. Add Malayalam-English only on request.\n"
                      f"Untrusted source excerpts:\n{source_block(sources)}")
            raw, info = await router.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                                              schema=LessonBody, fixture=fixture)
            result = {k: sanitize_citations(v, sources) for k, v in LessonBody.model_validate_json(raw).model_dump().items()}
            result.update(step=step, sources=source_info(sources), provider=info["provider"], action=body.action)
    except (Unavailable, ProviderError) as exc:
        raise HTTPException(503, str(exc)) from exc
    put(db, "lesson", key, result)
    db.commit()
    return lesson_public(result)
