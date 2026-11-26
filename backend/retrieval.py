import logging
import os
import re
import textwrap
import threading
import math
from collections import Counter
import sqlite_vec
from sqlalchemy import bindparam, select, text

from .db import DATA, POSTGRES, Chunk, Upload

logger = logging.getLogger(__name__)
_model = None
_lock = threading.Lock()
_model_unavailable = False


def words(text):
    return set(re.findall(r"[^\W_]{2,}", text.casefold())) - {
        "the", "an", "of", "and", "in", "for", "to", "a", "with", "is", "it", "on", "how", "what",
        "describe", "explain", "define", "state", "give", "discuss", "list", "calculate",
    }


def embedding(texts):
    global _model, _model_unavailable
    if os.getenv("BUDDY_EMBEDDINGS", "local") != "local" or _model_unavailable:
        return [None] * len(texts)
    try:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding
                _model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir=str(DATA / "models"), threads=2)
            return [sqlite_vec.serialize_float32(vector) for vector in _model.embed(texts, batch_size=32)]
    except Exception:
        _model_unavailable = True
        logger.warning("Local embeddings unavailable. Search will use lexical relevance.")
        return [None] * len(texts)


def map_topic(text, outline):
    terms = words(text)
    best = (0, None, None)
    for module in outline["modules"]:
        for topic in module["topics"]:
            candidates = words(topic)
            score = len(terms & candidates) / max(1, len(candidates)) + (1 if topic.casefold() in text.casefold() else 0)
            if score > best[0]:
                best = (score, module["id"], topic)
    if best[0]:
        return best[1:]
    for module in outline["modules"]:
        if re.search(r"\b(?:module|unit)\s+" + re.escape(module["id"]) + r"\b", text, re.I):
            return module["id"], None
    return None, None


def index_upload(db, upload, subject=None, *, pages=None):
    snippets = []
    for page in upload.pages if pages is None else pages:
        if upload.tag == "syllabus":
            blocks = page["text"].splitlines()
        else:
            blocks = re.split(r"\n\s*\n", page["text"])
        for block in blocks:
            snippets.extend((page["number"], excerpt) for excerpt in textwrap.wrap(
                block, width=1050, replace_whitespace=False, break_long_words=False
            ) if excerpt.strip())
    if not snippets:
        return
    vectors = embedding([excerpt for _, excerpt in snippets])
    for (page, excerpt), vector in zip(snippets, vectors):
        module, topic = map_topic(excerpt, subject.outline) if subject else (None, None)
        db.add(Chunk(upload_id=upload.id, page=page, text=excerpt, module=module, topic=topic, vector=vector))


def remap(db, subject):
    seen = db.scalars(select(Chunk).join(Upload).where(Upload.subject_id == subject.id))
    for chunk in seen:
        chunk.module, chunk.topic = map_topic(chunk.text, subject.outline)


def vector_similarity(first, second):
    left = memoryview(first).cast("f")
    right = memoryview(second).cast("f")
    norm = math.sqrt(sum(value * value for value in left) * sum(value * value for value in right))
    return max(0, sum(a * b for a, b in zip(left, right)) / norm) if norm else 0


def query(db, question, subject=None, module=None, topic=None, attachments=(), tags=None, limit=6):
    from sqlalchemy import and_, or_
    statement = select(Chunk, Upload).join(Upload)
    scope = Upload.subject_id.is_not(None) if subject is None else Upload.subject_id == subject
    if module:
        scope = and_(scope, Chunk.module == module)
    if topic:
        scope = and_(scope, Chunk.topic == topic)
    if tags:
        scope = and_(scope, Upload.tag.in_(tags))
    statement = statement.where(or_(scope, Upload.id.in_(attachments)))
    terms = words(question)
    candidates = db.execute(statement).all()
    vector_ids = [chunk.id for chunk, _ in candidates if chunk.vector is not None]
    vector = embedding([question])[0] if vector_ids else None
    similarities = {}
    if vector and POSTGRES:
        similarities = {chunk.id: vector_similarity(chunk.vector, vector)
                        for chunk, _ in candidates if chunk.vector is not None}
    elif vector:
        # Keep each scoped batch below SQLite's parameter and vec0 neighbor limits.
        knn = text("SELECT rowid, distance FROM chunk_vectors "
                   "WHERE embedding MATCH :embedding AND k = :neighbors AND rowid IN :ids").bindparams(
                       bindparam("ids", expanding=True))
        for start in range(0, len(vector_ids), 500):
            batch = vector_ids[start:start + 500]
            neighbors = db.execute(knn, {"embedding": vector, "neighbors": min(len(batch), limit * 5), "ids": batch})
            similarities.update((row_id, max(0, 1 - distance)) for row_id, distance in neighbors)
    ranked = []
    for chunk, upload in candidates:
        overlap = len(terms & words(chunk.text)) / max(1, len(terms))
        similarity = similarities.get(chunk.id, 0)
        attached = upload.id in attachments
        if not (overlap > 0 or similarity > .55 or attached or topic):
            continue
        priority = 2 if attached else 0
        score = priority + overlap + .4 * similarity
        ranked.append((score, chunk, upload))
    ranked.sort(key=lambda found: (-found[0], found[1].id))
    sources = []
    pages = set()
    for _, chunk, upload in ranked:
        key = (upload.id, chunk.page)
        if key in pages:
            continue
        pages.add(key)
        sources.append({"label": f"S{len(sources) + 1}", "name": upload.name, "page": chunk.page,
                        "upload_id": upload.id, "url": f"/api/uploads/{upload.id}/pages/{chunk.page}",
                        "text": chunk.text, "tag": upload.tag})
        if len(sources) == limit:
            break
    return sources


def coverage(db, subject):
    counts = Counter()
    uploads = db.execute(select(Chunk, Upload).join(Upload).where(Upload.subject_id == subject.id)).all()
    for chunk, upload in uploads:
        if upload.tag != "syllabus":
            counts[(chunk.module, chunk.topic, "material")] += 1
        if upload.tag == "notes":
            counts[(chunk.module, chunk.topic, "notes")] += 1
    return [{"id": module["id"], "name": module["name"], "topics": [
        {"name": topic, "material": counts[(module["id"], topic, "material")],
         "notes": counts[(module["id"], topic, "notes")]}
        for topic in module["topics"]]} for module in subject.outline["modules"]]
