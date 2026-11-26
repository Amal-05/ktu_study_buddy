import os
from pathlib import Path

import sqlite_vec
from dotenv import load_dotenv
from sqlalchemy import JSON, ForeignKey, LargeBinary, String, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA = Path(os.getenv("BUDDY_DATA", ROOT / ".study-data"))
DATA.mkdir(parents=True, exist_ok=True)


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


engine = create_engine(f"sqlite:///{(DATA / 'buddy.db').as_posix()}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def configure(connection, _):
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)


Session = sessionmaker(engine, expire_on_commit=False)


def session():
    with Session() as db:
        yield db


def migrate():
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
