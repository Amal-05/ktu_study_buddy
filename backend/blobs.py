from .db import Blob, POSTGRES, workspace_data


def store(db, key, content):
    if POSTGRES:
        db.add(Blob(key=key, content=content))
    else:
        (workspace_data() / key).write_bytes(content)


def load(db, key):
    if POSTGRES:
        row = db.get(Blob, key)
        return row.content if row else None
    path = workspace_data() / key
    return path.read_bytes() if path.is_file() else None


def discard_uncommitted(key):
    if not POSTGRES:
        (workspace_data() / key).unlink(missing_ok=True)
