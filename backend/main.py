import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import PurePath
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as Database
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .accounts import HostedAccess, ORIGIN, initialize, workspace_ids
from . import blobs
from .db import HOSTED, ROOT, Chunk, Provider, Subject, Upload, migrate, session, workspace
from .documents import MAX_BYTES, Outline, default_pattern, extract, parse_syllabus
from .providers import DEFAULT_LIMITS, ENDPOINTS, MOCK, ProviderError, cipher, endpoint, models, public


@asynccontextmanager
async def lifespan(app):
    from .study import recover_jobs, run_jobs
    if HOSTED:
        initialize()
        for identity in workspace_ids():
            with workspace(identity):
                migrate()
                recover_jobs()
    else:
        migrate()
        recover_jobs()
    worker = asyncio.create_task(run_jobs(workspace_ids if HOSTED else lambda: [None]))
    try:
        yield
    finally:
        worker.cancel()
        from contextlib import suppress
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="KTU Study Buddy", lifespan=lifespan, docs_url=None if HOSTED else "/docs",
              redoc_url=None if HOSTED else "/redoc", openapi_url=None if HOSTED else "/openapi.json")
if HOSTED:
    app.add_middleware(HostedAccess)
    from urllib.parse import urlsplit
    allowed_hostname = urlsplit(ORIGIN).hostname
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[allowed_hostname, "127.0.0.1"] if allowed_hostname else ["127.0.0.1"])
else:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
DB = Annotated[Database, Depends(session)]
upload_slots = asyncio.Semaphore(1 if os.getenv("RENDER") == "true" else 2)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Validation failures for provider forms must not echo submitted credentials.
    return JSONResponse({"detail": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                                    for item in exc.errors()]}, status_code=422)


@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if not HOSTED and request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
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

@app.get("/api/health")
def health():
    return {"status": "ok"}


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
    metadata = {key: value for key, value in upload.metadata_json.items() if key not in {"image", "original", "chat"}}
    metadata["downloadable"] = bool(upload.metadata_json.get("original"))
    return {"id": upload.id, "subject_id": upload.subject_id, "name": upload.name, "tag": upload.tag,
            "pages": upload.pages if pages else [{key: val for key, val in page.items() if key != "text"} for page in upload.pages],
            "metadata": metadata}


@app.get("/api/subjects/{subject_id}/uploads")
def uploads(subject_id: int, db: DB):
    require(db, Subject, subject_id)
    return [upload_data(row) for row in db.scalars(select(Upload).where(Upload.subject_id == subject_id).order_by(Upload.id.desc()))]


@app.get("/api/uploads/{upload_id}")
def upload_detail(upload_id: int, db: DB):
    return upload_data(require(db, Upload, upload_id), pages=True)


async def read_upload(file):
    try:
        name = PurePath((file.filename or "").replace(chr(92), "/")).name[:255]
        async with upload_slots:
            raw = await file.read(MAX_BYTES + 1)
            return name, raw, await run_in_threadpool(extract, name, raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "Could not read the document. Verify its format, encoding and size.") from exc


@app.post("/api/subjects/{subject_id}/uploads", status_code=201)
async def add_upload(subject_id: int, db: DB, file: Annotated[UploadFile, File()],
                     tag: Annotated[Literal["syllabus", "notes", "pyq", "textbook", "other"], Form()]):
    subject = require(db, Subject, subject_id)
    name, raw, pages = await read_upload(file)
    original = f"{uuid4().hex}.upload"
    metadata = {"original": original}
    row = Upload(subject_id=subject_id, name=name, tag=tag, pages=pages, metadata_json=metadata)
    if tag == "syllabus":
        row.metadata_json = {**metadata, "preview": parse_syllabus(pages)}
    try:
        blobs.store(db, original, raw)
        db.add(row)
        db.flush()
        from .retrieval import index_upload
        await run_in_threadpool(index_upload, db, row, subject)
        db.commit()
    except BaseException:
        blobs.discard_uncommitted(original)
        raise
    return upload_data(row, pages=True)


@app.get("/api/uploads/{upload_id}/download")
def download_upload(upload_id: int, db: DB):
    document = require(db, Upload, upload_id)
    original = document.metadata_json.get("original")
    content = blobs.load(db, original) if original else None
    if content is None:
        raise HTTPException(404, "The original file is unavailable")
    from urllib.parse import quote
    filename = quote(document.name, safe="")
    return Response(content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


class PageReview(BaseModel):
    text: str = Field(max_length=100000)


@app.put("/api/uploads/{upload_id}/pages/{page_number}")
def review_page(upload_id: int, page_number: int, body: PageReview, db: DB):
    from .retrieval import index_upload

    document = require(db, Upload, upload_id)
    if not any(page["number"] == page_number for page in document.pages):
        raise HTTPException(404, "Page does not exist")
    replacement = {"number": page_number, "text": body.text.strip(), "ocr_confidence": None, "review": False}
    document.pages = [replacement if page["number"] == page_number else page for page in document.pages]
    if document.tag == "syllabus":
        document.metadata_json = {**document.metadata_json, "preview": parse_syllabus(document.pages)}
    db.execute(delete(Chunk).where(Chunk.upload_id == upload_id, Chunk.page == page_number))
    subject = db.get(Subject, document.subject_id) if document.subject_id else None
    index_upload(db, document, subject, pages=[replacement])
    db.commit()
    return upload_data(document, pages=True)


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
        for key in ("requests_per_minute", "tokens_per_minute"):
            if type(merged[key]) is not int or not 0 <= merged[key] <= 20_000_000:
                raise ValueError("Per-minute limits must be nonnegative integers")
        for key in ("vision", "structured", "long_context"):
            if type(merged[key]) is not bool:
                raise ValueError("Provider capabilities must be boolean")
        return merged


@app.get("/api/providers")
def providers(db: DB):
    return {"providers": [public(row) for row in db.scalars(select(Provider).order_by(Provider.position, Provider.id))],
            "kinds": list(ENDPOINTS) + ([] if HOSTED else ["Custom"]), "defaults": DEFAULT_LIMITS}


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
