import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session as Database
from starlette.concurrency import run_in_threadpool

from .db import ROOT, Provider, Subject, Upload, migrate, session
from .documents import MAX_BYTES, Outline, default_pattern, extract, parse_syllabus
from .providers import DEFAULT_LIMITS, ENDPOINTS, MOCK, ProviderError, cipher, endpoint, models, public


@asynccontextmanager
async def lifespan(app):
    migrate()
    from .study import run_jobs
    worker = asyncio.create_task(run_jobs())
    try:
        yield
    finally:
        worker.cancel()
        from contextlib import suppress
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="KTU Study Buddy", lifespan=lifespan)
DB = Annotated[Database, Depends(session)]


@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        from urllib.parse import urlsplit
        if urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "Only the local app may change study data"}, status_code=403)
    return await call_next(request)


def require(db, model, row_id):
    row = db.get(model, row_id)
    if row is None:
        raise HTTPException(404, "Record does not exist")
    return row


class SubjectInput(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    semester: int = Field(ge=1, le=8)
    branch: str = Field(min_length=1, max_length=100)
    exam_date: date


def subject_data(row):
    return {key: getattr(row, key) for key in ("id", "code", "name", "semester", "branch", "exam_date", "outline")}


@app.get("/api/status")
def status():
    return {"mock": MOCK, "search_mock": os.getenv("SEARCH_MOCK", "1") == "1"}


@app.get("/api/subjects")
def subjects(db: DB):
    return [subject_data(row) for row in db.scalars(select(Subject).order_by(Subject.exam_date, Subject.id))]


@app.post("/api/subjects", status_code=201)
def create_subject(body: SubjectInput, db: DB):
    row = Subject(**body.model_dump(mode="json"), outline={"modules": [], "pattern": default_pattern(), "outcomes": [], "textbooks": [], "pattern_evidence": []})
    db.add(row)
    db.commit()
    return subject_data(row)


@app.put("/api/subjects/{subject_id}")
def update_subject(subject_id: int, body: SubjectInput, db: DB):
    row = require(db, Subject, subject_id)
    for key, value in body.model_dump(mode="json").items():
        setattr(row, key, value)
    db.commit()
    return subject_data(row)


@app.put("/api/subjects/{subject_id}/outline")
def update_outline(subject_id: int, body: Outline, db: DB):
    row = require(db, Subject, subject_id)
    row.outline = body.model_dump()
    from .retrieval import remap
    remap(db, row)
    db.commit()
    return subject_data(row)


def upload_data(upload, pages=False):
    return {"id": upload.id, "subject_id": upload.subject_id, "name": upload.name, "tag": upload.tag,
            "pages": upload.pages if pages else [{key: val for key, val in page.items() if key != "text"} for page in upload.pages],
            "metadata": upload.metadata_json}


@app.get("/api/subjects/{subject_id}/uploads")
def uploads(subject_id: int, db: DB):
    require(db, Subject, subject_id)
    return [upload_data(row) for row in db.scalars(select(Upload).where(Upload.subject_id == subject_id).order_by(Upload.id.desc()))]


@app.get("/api/uploads/{upload_id}")
def upload_detail(upload_id: int, db: DB):
    return upload_data(require(db, Upload, upload_id), pages=True)


async def read_upload(file):
    try:
        raw = await file.read(MAX_BYTES + 1)
        name = PurePath((file.filename or "").replace(chr(92), "/")).name[:255]
        return name, raw, await run_in_threadpool(extract, name, raw)
    except Exception as exc:
        raise HTTPException(422, "Could not read the document. Verify its format, encoding and size.") from exc


@app.post("/api/subjects/{subject_id}/uploads", status_code=201)
async def add_upload(subject_id: int, db: DB, file: Annotated[UploadFile, File()],
                     tag: Annotated[Literal["syllabus", "notes", "pyq", "textbook", "other"], Form()]):
    subject = require(db, Subject, subject_id)
    name, _, pages = await read_upload(file)
    row = Upload(subject_id=subject_id, name=name, tag=tag, pages=pages, metadata_json={})
    if tag == "syllabus":
        row.metadata_json = {"preview": parse_syllabus(pages)}
    db.add(row)
    db.flush()
    from .retrieval import index_upload
    index_upload(db, row, subject)
    db.commit()
    return upload_data(row, pages=True)


class ProviderInput(BaseModel):
    kind: Literal["Gemini", "OpenAI", "Groq", "OpenRouter", "Mistral", "Custom"]
    name: str = Field(default="", max_length=80)
    base_url: str = Field(default="", max_length=300)
    model: str = Field(min_length=1, max_length=180)
    key: str = Field(default="", max_length=2048)
    enabled: bool = True
    position: int = Field(default=0, ge=0, le=200)
    limits: dict = Field(default_factory=dict)

    @field_validator("limits")
    @classmethod
    def valid_limits(cls, limits):
        merged = {**DEFAULT_LIMITS, **{key: val for key, val in limits.items() if key in DEFAULT_LIMITS}}
        if type(merged["context"]) is not int or type(merged["max_output"]) is not int:
            raise ValueError("Context and output limits must be integers")
        if not 1024 <= merged["context"] <= 2_000_000 or not 16 <= merged["max_output"] < merged["context"] - 512:
            raise ValueError("Set positive output and context limits with room for the prompt")
        return merged


@app.get("/api/providers")
def providers(db: DB):
    return {"providers": [public(row) for row in db.scalars(select(Provider).order_by(Provider.position, Provider.id))],
            "kinds": list(ENDPOINTS) + ["Custom"], "defaults": DEFAULT_LIMITS}


@app.post("/api/providers", status_code=201)
@app.put("/api/providers/{provider_id}")
def save_provider(body: ProviderInput, db: DB, provider_id: int | None = None):
    row = require(db, Provider, provider_id) if provider_id is not None else Provider()
    if provider_id is None and not body.key.strip():
        raise HTTPException(422, "Enter an API key")
    if body.kind != "Custom" and db.scalar(select(Provider).where(Provider.kind == body.kind, Provider.id != (provider_id or 0))):
        raise HTTPException(409, "This provider already has a key")
    try:
        row.base_url = endpoint(body.kind, body.base_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row.kind, row.name, row.model = body.kind, body.name or body.kind, body.model.strip()
    row.position, row.enabled, row.limits = body.position, body.enabled, {**DEFAULT_LIMITS, **body.limits}
    if body.key.strip():
        row.secret = cipher().encrypt(body.key.strip().encode())
        row.last_four, row.cooldown, row.status = body.key.strip()[-4:], 0, "Ready"
    db.add(row)
    db.commit()
    return public(row)


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: int, db: DB):
    db.delete(require(db, Provider, provider_id))
    db.commit()
    return {"deleted": True}


@app.post("/api/providers/{provider_id}/test")
async def test_provider(provider_id: int, db: DB):
    row = require(db, Provider, provider_id)
    try:
        found = await models(row)
    except ProviderError as exc:
        row.status = str(exc)
        if exc.action == "disable":
            row.enabled = False
        if exc.reset:
            row.cooldown = exc.reset
        db.commit()
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "Could not fetch the provider's model catalog") from exc
    row.status = "Ready"
    row.cooldown = 0
    db.commit()
    return {"models": found, "configured_model_found": row.model in found, "provider": public(row)}


def mount_frontend():
    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")

    @app.get("/")
    def home():
        if not (dist / "index.html").exists():
            return {"app": "KTU Study Buddy", "message": "Build the frontend or run python start.py."}
        return FileResponse(dist / "index.html")
