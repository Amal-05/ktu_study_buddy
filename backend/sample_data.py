from datetime import date, timedelta

from fastapi import APIRouter
from sqlalchemy import select

from sample.reference import TOPICS

from .db import ROOT, Subject, Upload
from .documents import Outline, extract, parse_syllabus
from .main import DB, subject_data
from .retrieval import index_upload

api = APIRouter(prefix="/api")


@api.post("/sample")
def load_sample(db: DB):
    prior = db.scalar(select(Subject).where(Subject.code == "DEMO201"))
    if prior:
        return subject_data(prior)
    name = "sample-syllabus.md"
    syllabus = extract(name, (ROOT / "sample" / name).read_bytes())
    outline = Outline.model_validate(parse_syllabus(syllabus)).model_dump()
    subject = Subject(code="DEMO201", name="Data Structures and Algorithms", semester=3, branch="Computer Science",
                      exam_date=(date.today() + timedelta(days=14)).isoformat(), outline=outline)
    db.add(subject)
    db.flush()
    reference = "\f".join(f"{topic}\n\n{values[0]}\n\n{values[1]}\n\n{values[4]}" for topic, values in TOPICS.items())
    sources = [(name, "syllabus", syllabus), ("sample-reference.txt", "textbook", extract("sample-reference.txt", reference.encode()))]
    for title, tag, pages in sources:
        document = Upload(subject_id=subject.id, name=title, tag=tag, pages=pages, metadata_json={"sample": True})
        db.add(document)
        db.flush()
        index_upload(db, document, subject)
    db.commit()
    return subject_data(subject)
