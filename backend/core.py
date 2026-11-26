from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from .db import Record


def get(db, kind, key, default=None):
    row = db.scalar(select(Record).where(Record.kind == kind, Record.key == str(key)))
    return row.data if row else default


def put(db, kind, key, data):
    db.execute(insert(Record).values(kind=kind, key=str(key), data=data)
               .on_conflict_do_update(index_elements=["kind", "key"], set_={"data": data}))
    return data


def records(db, kind):
    return db.execute(select(Record.key, Record.data).where(Record.kind == kind).order_by(Record.id.desc())).all()
