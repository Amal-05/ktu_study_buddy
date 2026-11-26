import json
import time
from io import BytesIO
from types import SimpleNamespace

from docx import Document

from backend.chat import sanitize_citations
from backend.db import ROOT
from backend.documents import Outline, extract, parse_syllabus
from backend.exams import backtest, extract_questions, generate_paper, score_topics


def sample_outline():
    path = ROOT / "sample" / "sample-syllabus.md"
    return Outline.model_validate(parse_syllabus(extract(path.name, path.read_bytes()))).model_dump()


def test_word_explicit_page_break_keeps_document_page_labels():
    document = Document()
    document.add_paragraph("The first page")
    document.add_page_break()
    document.add_paragraph("The second page")
    file = BytesIO()
    document.save(file)
    pages = extract("notes.docx", file.getvalue())
    assert [(page["number"], page["text"]) for page in pages] == [(1, "The first page"), (2, "The second page")]


def test_sample_syllabus_and_default_paper():
    outline = sample_outline()
    assert len(outline["modules"]) == 4
    assert outline["modules"][2]["hours"] == 13
    assert outline["modules"][0]["outcomes"] == ["CO1"]
    assert "Binary search" in outline["modules"][3]["topics"]
    assert outline["textbooks"]
    assert outline["pattern"]["cie"] == 40
    assert outline["pattern"]["ese"] == 60
    paper = generate_paper(outline, score_topics(outline, []))
    assert paper["total"] == 60
    part_a, part_b = paper["sections"]
    assert all(len(module["questions"]) == module["attempt"] == 2 for module in part_a["groups"])
    assert all(len(module["questions"]) == 2 and module["attempt"] == 1 for module in part_b["groups"])
    assert all(q["generated"] for part in paper["sections"] for group in part["groups"] for q in group["questions"])


def test_pattern_consumes_subject_configuration():
    outline = sample_outline()
    outline["pattern"]["ese"] = 40
    outline["pattern"]["parts"] = [{"name": "X", "offered": 3, "attempt": 2, "marks": 5, "subdivisions": 2}]
    validated = Outline.model_validate(outline).model_dump()
    paper = generate_paper(validated, [])
    assert paper["total"] == 40
    assert paper["sections"][0]["max_subdivisions"] == 2
    assert all(len(group["questions"]) == 3 for group in paper["sections"][0]["groups"])


def test_whole_paper_choice_uses_single_group():
    outline = sample_outline()
    outline["pattern"]["ese"] = 20
    outline["pattern"]["parts"] = [{"name": "X", "scope": "paper", "offered": 6, "attempt": 4, "marks": 5}]
    checked = Outline.model_validate(outline).model_dump()
    paper = generate_paper(checked, score_topics(checked, []))
    group, = paper["sections"][0]["groups"]
    assert paper["total"] == 20
    assert len(group["questions"]) == 6 and group["attempt"] == 4
    assert all(q["module"] != "all" for q in group["questions"])


def test_upload_preserves_original_and_updates_reviewed_page(client):
    subject = client.post("/api/subjects", json={"code": "OCRTEST", "name": "Upload test", "semester": 2,
                                                "branch": "Computer Science", "exam_date": "2029-01-01"}).json()
    upload = client.post(f"/api/subjects/{subject['id']}/uploads", data={"tag": "notes"},
                         files={"file": ("notes.txt", b"Original page contents")}).json()
    page = f"/api/uploads/{upload['id']}/pages/1"
    assert client.get(page).text == "Original page contents"
    assert client.put(page, json={"text": "Corrected page contents"}).is_success
    assert client.get(page).text == "Corrected page contents"
    assert client.get(f"/api/uploads/{upload['id']}/download").content == b"Original page contents"


def make_paper(identifier, year, scheme, topic):
    paper = f"Year: {year}\n{scheme} scheme\nPart A\n1. Explain {topic} with an example. (3)"
    return SimpleNamespace(pages=[{"text": paper, "number": 1}], metadata_json={}, name="Test fixture", id=identifier)


def test_prediction_evidence_scoring_and_holdout():
    outline = sample_outline()
    old = extract_questions(make_paper(1, 2022, 2019, "Binary search"), outline)
    recent = extract_questions(make_paper(2, 2026, 2024, "Binary search"), outline)
    assert old and recent and recent[0]["marks"] == 3
    old_score = next(row for row in score_topics(outline, old, 2026) if row["topic"] == "Binary search")
    new_score = next(row for row in score_topics(outline, recent, 2026) if row["topic"] == "Binary search")
    assert new_score["score"] > old_score["score"]
    assert backtest(outline, old + recent)["paper"] == 2


def test_citation_allowlist_and_live_pages(client):
    sample = client.post("/api/sample").json()
    upload = client.get(f"/api/subjects/{sample['id']}/uploads").json()[0]
    assert client.get(f"/api/uploads/{upload['id']}/pages/1").status_code == 200
    assert client.get(f"/api/uploads/{upload['id']}/pages/9999").status_code == 404
    assert sanitize_citations("Source [S1], unknown [S55].", [{"label": "S1"}]) == "Source [S1], unknown ."


def test_sample_end_to_end(client):
    sample = client.post("/api/sample").json()
    base = f"/api/subjects/{sample['id']}"
    scope = {"module": "1", "topic": "Stacks and queues"}
    job = client.post(base + "/notes/jobs", json=scope).json()
    for _ in range(70):
        result = client.get(f"/api/jobs/{job['id']}").json()
        if result["state"] in {"done", "failed"}:
            break
        time.sleep(.1)
    assert result["state"] == "done", result
    note = client.get(base + "/notes", params=scope).json()
    version = note["versions"][0]
    assert "# Stacks and queues" in version["text"]
    assert all(client.get(item["url"]).is_success for item in version["sources"])
    pdf = client.get(base + "/notes/export", params={**scope, "version": version["id"], "format": "pdf"})
    assert pdf.content.startswith(b"%PDF"), pdf.text[:100] if not pdf.is_success else ""
    edited = client.put(base + "/notes", json={**scope, "version": version["id"], "text": "My own notes"}).json()
    assert edited["versions"][0]["edited"]
    lesson = client.post(base + "/lesson", json=scope).json()
    assert lesson["step"] >= 1 and "expected_answer" not in lesson
    answer = client.post(base + "/lesson", json={**scope, "action": "answer", "answer": "A"}).json()
    assert answer["evaluation"]["correct"]
    attempt = client.post(base + "/mock").json()
    assert attempt["paper"]["total"] == 60
    q = next(q for part in attempt["paper"]["sections"] for group in part["groups"] for q in group["questions"] if q["topic"] == scope["topic"])
    result = client.post("/api/mock/" + attempt["id"] + "/submit", json={"answers": {
        q["id"]: "A stack removes the newest item first (LIFO). A queue removes the oldest item first (FIFO)."}}).json()
    assert result["possible"] == 60 and result["awarded"] > 0
    summary = client.get("/api/dashboard").json()["subjects"]
    assert next(row for row in summary if row["id"] == sample["id"])["mastery"] > 0
    chat = client.post("/api/chats", json={"subject_id": sample["id"], **scope}).json()
    reply = client.post(f"/api/chats/{chat['id']}/stream", json={"text": "How do stacks and queues work?",
                                                               "subject_id": sample["id"], **scope})
    events = [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "done"
    assert events[-1]["message"]["sources"]
