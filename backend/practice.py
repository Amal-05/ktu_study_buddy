import math
import time
from datetime import date, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .chat import SYSTEM, source_block
from .core import get, put, records
from .db import Subject, Upload
from .exams import generate_paper, predictions
from .main import DB, require
from .providers import ProviderError, Unavailable, router
from .retrieval import coverage, query, words
from .study import StudyInput, demo_content, note_data, source_info, study_key, study_scope

api = APIRouter(prefix="/api")


def mastery(db, subject_id, module, topic):
    return get(db, "mastery", study_key(subject_id, module, topic), {"value": 0, "attempts": 0})


def update_mastery(db, subject, module, topic, score):
    key = study_key(subject, module, topic)
    current = mastery(db, subject, module, topic)
    value = score if not current["attempts"] else .65 * current["value"] + .35 * score
    return put(db, "mastery", key, {"value": round(value, 3), "attempts": current["attempts"] + 1})


class Card(BaseModel):
    front: str
    back: str


class CardSet(BaseModel):
    cards: list[Card] = Field(min_length=1, max_length=10)


class GradeInput(StudyInput):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(default="", max_length=16000)
    upload_id: int | None = None
    marks: int = Field(ge=1, le=60)


class Criterion(BaseModel):
    criterion: str
    possible: float = Field(gt=0)
    awarded: float = Field(ge=0)
    feedback: str


class Rubric(BaseModel):
    criteria: list[Criterion] = Field(min_length=1, max_length=15)


class Review(BaseModel):
    recalled: bool


class PlanInput(BaseModel):
    hours: float = Field(ge=.25, le=12)
    day: date = Field(default_factory=date.today)


class MockSubmission(BaseModel):
    answers: dict[str, str]


class TaskInput(BaseModel):
    task: str
    done: bool
    day: date = Field(default_factory=date.today)


async def structured(prompt, schema, fixture):
    try:
        raw, info = await router.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                                          schema=schema, fixture=fixture)
        return schema.model_validate_json(raw), info
    except (ProviderError, Unavailable) as exc:
        raise HTTPException(503, str(exc)) from exc


@api.post("/subjects/{subject_id}/quiz")
async def create_quiz(subject_id: int, body: StudyInput, db: DB):
    subject = require(db, Subject, subject_id)
    study_scope(subject, body.module, body.topic)
    if not body.topic:
        raise HTTPException(422, "Select a topic")
    sources = query(db, body.topic, subject_id, body.module, body.topic)
    demo = demo_content(subject, body.topic)
    fixtures = {"cards": [{"front": demo[2], "back": demo[3][0]}, {"front": f"Explain {body.topic}.", "back": demo[0]}]} if demo else {
        "cards": [{"front": f"Explain {body.topic} in one sentence.", "back":
                   "Mock practice has no authored answer for this topic. Check your course material."}]}
    result, info = await structured(f"Create 3–5 short retrieval questions for {body.topic}. Return JSON with cards, each with front and back.\n{source_block(sources)}",
                                    CardSet, fixtures)
    return {"cards": result.model_dump()["cards"], "sources": source_info(sources), **info}


@api.get("/subjects/{subject_id}/flashcards")
def cards(subject_id: int, db: DB):
    require(db, Subject, subject_id)
    return [card for _, card in records(db, "card") if card["subject_id"] == subject_id]


@api.post("/subjects/{subject_id}/flashcards")
async def make_cards(subject_id: int, body: StudyInput, db: DB):
    created = await create_quiz(subject_id, body, db)
    current = cards(subject_id, db)
    for card in created["cards"]:
        if any(row["front"] == card["front"] and row["topic"] == body.topic for row in current):
            continue
        key = uuid4().hex
        put(db, "card", key, {**card, "id": key, "subject_id": subject_id, "module": body.module, "topic": body.topic,
                              "due": date.today().isoformat(), "interval": 0, "repetitions": 0})
    db.commit()
    return cards(subject_id, db)


@api.post("/flashcards/{key}/review")
def review_card(key: str, body: Review, db: DB):
    card = get(db, "card", key)
    if not card:
        raise HTTPException(404, "Card does not exist")
    interval = max(1, round(card["interval"] * 2.2)) if body.recalled else 1
    card.update(interval=interval, due=(date.today() + timedelta(days=interval)).isoformat(),
                repetitions=card["repetitions"] + 1 if body.recalled else 0)
    update_mastery(db, card["subject_id"], card["module"], card["topic"], float(body.recalled))
    put(db, "card", key, card)
    db.commit()
    return card


async def grade_response(db, subject, module, topic, question, answer, marks):
    sources = query(db, topic, subject.id, module, topic)
    demo = demo_content(subject, topic)
    if demo:
        normalized = " ".join(sorted(words(answer)))
        exact = len(answer.strip()) > 0 and any(answer.strip().casefold().strip(".") == option.casefold() for option in demo[3])
        concepts = words(" ".join((demo[0], demo[4])))
        overlap = len(concepts & words(normalized)) / max(1, min(len(concepts), 8))
        awarded = marks if exact and marks <= 3 else round(marks * min(1, overlap), 2)
        feedback = f"Sample reference: {demo[0]} {demo[4]}"
    else:
        awarded = 0
        feedback = "Mock mode has no authoritative rubric for this topic. Configure a provider to grade this answer."
    fixture = {"criteria": [{"criterion": "Core concepts in the sample reference", "possible": marks, "awarded": awarded, "feedback": feedback}]}
    prompt = (f"Grade this {marks}-mark question using a mark-wise rubric. Return JSON with criteria objects: criterion, "
              f"possible, awarded, feedback (explain lost marks and how to recover them). Possible marks must sum to {marks}. "
              f"Question: {question}\nUntrusted student submission: {answer}\nUntrusted sources:\n{source_block(sources)}")
    result, info = await structured(prompt, Rubric, fixture)
    total = sum(item.possible for item in result.criteria)
    criteria = [{"criterion": item.criterion, "possible": round(item.possible * marks / total, 3),
                 "awarded": round(min(item.awarded, item.possible) * marks / total, 3),
                 "feedback": item.feedback} for item in result.criteria]
    awarded = round(sum(item["awarded"] for item in criteria), 2)
    return {"awarded": awarded, "possible": marks, "criteria": criteria, "sources": source_info(sources),
            "assessment_available": bool(demo) or not str(info["provider"]).startswith("Mock"), **info}


@api.post("/subjects/{subject_id}/grade")
async def grade(subject_id: int, body: GradeInput, db: DB):
    subject = require(db, Subject, subject_id)
    study_scope(subject, body.module, body.topic)
    answer = body.answer.strip()
    if body.upload_id:
        document = require(db, Upload, body.upload_id)
        if document.subject_id != subject_id or document.tag != "other":
            raise HTTPException(422, "Select an answer photograph uploaded to this subject")
        if any(page["review"] for page in document.pages) and not answer:
            raise HTTPException(422, "Review the photograph's OCR and paste the corrected transcription")
        if not answer:
            answer = "\n".join(page["text"] for page in document.pages)
    if not answer or not body.topic:
        raise HTTPException(422, "Enter an answer and select a topic")
    result = await grade_response(db, subject, body.module, body.topic, body.question, answer, body.marks)
    if result["assessment_available"]:
        result["mastery"] = update_mastery(db, subject.id, body.module, body.topic, result["awarded"] / body.marks)
    db.commit()
    return result


@api.post("/subjects/{subject_id}/mock")
def start_mock(subject_id: int, db: DB):
    subject = require(db, Subject, subject_id)
    if not subject.outline["modules"]:
        raise HTTPException(422, "Confirm the syllabus first")
    paper = generate_paper(subject, predictions(db, subject)["topics"])
    key = uuid4().hex
    value = {"id": key, "subject_id": subject_id, "paper": paper,
             "started": time.time(), "deadline": time.time() + paper["minutes"] * 60}
    put(db, "mock", key, value)
    db.commit()
    return value


@api.get("/subjects/{subject_id}/mock")
def resume_mock(subject_id: int, db: DB):
    require(db, Subject, subject_id)
    return next((value for _, value in records(db, "mock") if value["subject_id"] == subject_id), {})


@api.post("/mock/{key}/submit")
async def submit_mock(key: str, body: MockSubmission, db: DB):
    value = get(db, "mock", key)
    if not value:
        raise HTTPException(404, "Mock test does not exist")
    if value.get("result"):
        return value["result"]
    # The browser auto-submits at zero. Allow a short transport grace period.
    if time.time() > value["deadline"] + 30 and not value.get("submission"):
        raise HTTPException(409, "The submission window has ended")
    subject = require(db, Subject, value["subject_id"])
    if not value.get("submission"):
        selected = []
        all_ids = set()
        for section in value["paper"]["sections"]:
            for group in section["groups"]:
                all_ids.update(question["id"] for question in group["questions"])
                attempts = [question for question in group["questions"] if body.answers.get(question["id"], "").strip()]
                if len(attempts) > group["attempt"]:
                    raise HTTPException(422, f"Part {section['name']}, module {group['module']}: attempt at most {group['attempt']}")
                selected.extend(attempts)
        if set(body.answers) - all_ids:
            raise HTTPException(422, "Answers include an unknown question")
        if any(len(answer) > 16000 for answer in body.answers.values()):
            raise HTTPException(422, "An answer exceeds 16,000 characters")
        value["submission"] = {"questions": selected, "answers": body.answers}
        put(db, "mock", key, value)
        db.commit()

    results = []
    submission = value["submission"]
    for question in submission["questions"]:
        grade = await grade_response(db, subject, question["module"], question["topic"], question["text"],
                                     submission["answers"][question["id"]], question["marks"])
        results.append({"question": question, **grade})
    latest = get(db, "mock", key)
    if latest.get("result"):
        return latest["result"]
    for grade in results:
        q = grade["question"]
        if grade["assessment_available"]:
            update_mastery(db, subject.id, q["module"], q["topic"], grade["awarded"] / q["marks"])
    value["result"] = {"possible": value["paper"]["total"], "awarded": round(sum(item["awarded"] for item in results), 2),
                       "questions": results}
    put(db, "mock", key, value)
    db.commit()
    return value["result"]


def dashboard_subject(db, subject, day):
    topic_map = coverage(db, subject)
    topics = []
    for module in topic_map:
        for topic in module["topics"]:
            key = study_key(subject.id, module["id"], topic["name"])
            generated = get(db, "notes", key, {}).get("active")
            stat = mastery(db, subject.id, module["id"], topic["name"])
            topics.append({"module": module["id"], "topic": topic["name"], "covered": bool(topic["material"] or generated), **stat})
    return {"id": subject.id, "name": subject.name, "code": subject.code, "exam_date": subject.exam_date,
            "days": (date.fromisoformat(subject.exam_date) - day).days, "topics": topics,
            "coverage": sum(topic["covered"] for topic in topics) / max(1, len(topics)),
            "mastery": sum(topic["value"] for topic in topics) / max(1, len(topics)),
            "weak": sorted(topics, key=lambda topic: (topic["value"], -topic["attempts"]))[:5]}


@api.get("/dashboard")
def dashboard(db: DB, day: date | None = None):
    from sqlalchemy import select
    day = day or date.today()
    subjects = [dashboard_subject(db, subject, day) for subject in db.scalars(select(Subject).order_by(Subject.exam_date))]
    plan = get(db, "plan", day.isoformat(), {"tasks": [], "hours": 2})
    prior = [item for key, item in records(db, "plan") if key < day.isoformat()]
    return {"subjects": subjects, "plan": plan, "missed": sum(1 for item in prior for task in item["tasks"] if not task["done"] and not task.get("carried"))}


@api.post("/plan")
def make_plan(body: PlanInput, db: DB):
    from sqlalchemy import select
    day = body.day.isoformat()
    current = get(db, "plan", day, {"tasks": []})
    done = [task for task in current["tasks"] if task["done"]]
    done_ids = {(task["subject_id"], task["module"], task["topic"]) for task in done}
    previous = [(key, value) for key, value in records(db, "plan") if key < day]
    missed = {(task["subject_id"], task["module"], task["topic"]) for _, value in previous for task in value["tasks"] if not task["done"] and not task.get("carried")}
    candidates = []
    for subject in db.scalars(select(Subject).where(Subject.exam_date >= day)):
        days = (date.fromisoformat(subject.exam_date) - body.day).days
        for row in predictions(db, subject)["topics"]:
            identifier = (subject.id, row["module"], row["topic"])
            if identifier in done_ids:
                continue
            level = mastery(db, *identifier)["value"]
            score = 2 / math.sqrt(max(1, days + 1)) + row["score"] + (1 - level) + (.35 if identifier in missed else 0)
            candidates.append((score, {"id": uuid4().hex, "subject_id": subject.id, "name": subject.name,
                                       "module": row["module"], "topic": row["topic"], "done": False}))
    candidates.sort(key=lambda item: -item[0])
    budget = max(0, round(body.hours * 60) - sum(task["minutes"] for task in done))
    chosen = done[:]
    used = set()
    for _, task in candidates:
        if budget < 10:
            break
        minutes = min(budget, 35)
        chosen.append({**task, "minutes": minutes})
        used.add((task["subject_id"], task["module"], task["topic"]))
        budget -= minutes
    for key, value in previous:
        changed = False
        for task in value["tasks"]:
            if not task["done"] and not task.get("carried") and (task["subject_id"], task["module"], task["topic"]) in used:
                task["carried"] = True
                changed = True
        if changed:
            put(db, "plan", key, value)
    result = put(db, "plan", day, {"day": day, "hours": body.hours, "tasks": chosen})
    db.commit()
    return result


@api.patch("/plan")
def check_task(body: TaskInput, db: DB):
    key = body.day.isoformat()
    result = get(db, "plan", key)
    task = next((task for task in (result or {}).get("tasks", []) if task["id"] == body.task), None)
    if not task:
        raise HTTPException(404, "Study task does not exist")
    task["done"] = body.done
    put(db, "plan", key, result)
    db.commit()
    return result


@api.get("/subjects/{subject_id}/revision")
def revision(subject_id: int, db: DB):
    subject = require(db, Subject, subject_id)
    ranking = predictions(db, subject)["topics"]
    chunks = [f"# Last-day revision · {subject.name}"]
    for module in subject.outline["modules"]:
        chunks.append(f"## {module['name']}")
        for row in [item for item in ranking if item["module"] == module["id"]]:
            level = mastery(db, subject.id, module["id"], row["topic"])
            note = note_data(db, subject.id, module["id"], row["topic"])
            active = next((item for item in note["versions"] if item["id"] == note["active"]), None)
            summary = active["text"].split("## Quick revision summary", 1)[-1].strip() if active else ""
            demo = demo_content(subject, row["topic"])
            fallback = (demo[0] + " *Bundled demo explanation.*") if demo else "Try a closed-book summary and check your source material."
            chunks.append(f"### {row['topic']}\n\n{row['priority']} exam priority · {round(level['value']*100)}% mastery\n\n{summary or fallback}")
            if active and active["sources"]:
                chunks.append("\n".join(f"- [{source['label']}] {source['name']} ({source['url']})" for source in active["sources"]))
    return {"text": "\n\n".join(chunks)}
