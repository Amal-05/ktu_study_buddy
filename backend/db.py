import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import sqlite_vec
from dotenv import load_dotenv
from sqlalchemy import JSON, ForeignKey, LargeBinary, String, UniqueConstraint, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA = Path(os.getenv("BUDDY_DATA", ROOT / ".study-data"))
DATA.mkdir(parents=True, exist_ok=True)
HOSTED = os.getenv("BUDDY_HOSTED", "0") == "1"
DATABASE_URL = os.getenv("BUDDY_DATABASE_URL", "")
POSTGRES = HOSTED and bool(DATABASE_URL)
_workspace = ContextVar("study_workspace", default=None)


def workspace_id():
    return _workspace.get()


def workspace_data():
    identity = workspace_id()
    return DATA / "workspaces" / identity if identity else DATA


@contextmanager
def workspace(identity):
    token = _workspace.set(identity)
    try:
        yield
    finally:
        _workspace.reset(token)


class Base(DeclarativeBase):
    pass


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str]
    semester: Mapped[int]
    branch: Mapped[str]
    exam_date: Mapped[str]
    outline: Mapped[dict] = mapped_column(JSON)


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    name: Mapped[str]
    tag: Mapped[str]
    pages: Mapped[list] = mapped_column(JSON)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True)
    page: Mapped[int]
    text: Mapped[str]
    module: Mapped[str | None]
    topic: Mapped[str | None]
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)


class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    name: Mapped[str]
    base_url: Mapped[str]
    model: Mapped[str]
    secret: Mapped[bytes] = mapped_column(LargeBinary)
    last_four: Mapped[str]
    position: Mapped[int]
    enabled: Mapped[bool] = mapped_column(default=True)
    cooldown: Mapped[float] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="Ready")
    calls: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
    limits: Mapped[dict] = mapped_column(JSON)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (UniqueConstraint("kind", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]
    key: Mapped[str]
    data: Mapped[dict] = mapped_column(JSON)


class Blob(Base):
    __tablename__ = "blobs"
    key: Mapped[str] = mapped_column(primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)


def configure(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)


@lru_cache(maxsize=256)
def workspace_engine(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = create_engine(URL.create("sqlite", database=str(directory / "buddy.db")),
                           connect_args={"check_same_thread": False, "timeout": 10}, poolclass=NullPool)
    event.listen(result, "connect", configure)
    return result


engine = workspace_engine(DATA)


@lru_cache(maxsize=1)
def cloud_engine():
    url = make_url(DATABASE_URL)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise RuntimeError("BUDDY_DATABASE_URL must use PostgreSQL")
    if os.getenv("RENDER") == "true" and url.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        raise RuntimeError("Use the Neon PostgreSQL connection string with SSL enabled")
    return create_engine(url.set(drivername="postgresql+psycopg"), pool_pre_ping=True,
                         pool_size=4, max_overflow=4, pool_timeout=15, hide_parameters=True,
                         connect_args={"prepare_threshold": None, "connect_timeout": 12})


def schema_for(identity):
    if not identity or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise RuntimeError("Invalid PostgreSQL workspace identifier")
    return "study_" + identity


def Session():
    if HOSTED and not workspace_id():
        raise RuntimeError("Study data requires an authenticated workspace in hosted mode")
    if POSTGRES:
        bind = cloud_engine().execution_options(schema_translate_map={None: schema_for(workspace_id())})
    else:
        bind = workspace_engine(workspace_data())
    return sessionmaker(bind, expire_on_commit=False)()


def session():
    with Session() as db:
        yield db


def migrate(existing_connection=None):
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    def upgrade(connection):
        if POSTGRES:
            schema = schema_for(workspace_id())
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(820034913)")
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        else:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    if existing_connection is not None:
        upgrade(existing_connection)
    else:
        database = cloud_engine() if POSTGRES else workspace_engine(workspace_data())
        with database.begin() as connection:
            upgrade(connection)
