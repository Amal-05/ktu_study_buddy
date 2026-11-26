import os
import subprocess
import sys
import time
from pathlib import Path


def test_hosted_isolation_and_recovery(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "BUDDY_HOSTED": "1", "BUDDY_DATA": str(tmp_path),
           "BUDDY_PUBLIC_ORIGIN": "https://study.example.test", "BUDDY_OPEN_SIGNUP": "1",
           "BUDDY_MAX_ACCOUNTS": "4", "BUDDY_EMBEDDINGS": "lexical", "PYTHONPATH": str(root)}
    result = subprocess.run([sys.executable, __file__], env=env, cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def run_hosted_scenario():
    from fastapi.testclient import TestClient

    from backend.server import app

    origin = "https://study.example.test"
    headers = {"Origin": origin}
    one = {"username": "student.one", "password": "strong passphrase one"}
    two = {"username": "student.two", "password": "strong passphrase two"}
    subject = {"name": "Private subject", "code": "PRIV101", "semester": 3,
               "branch": "Computer Science", "exam_date": "2028-03-02"}

    bob = TestClient(app, base_url=origin)
    with TestClient(app, base_url=origin) as alice:
        unauthenticated = alice.get("/api/subjects")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in unauthenticated.headers["Content-Security-Policy"]
        assert alice.get("/docs").status_code == 404
        assert alice.post("/api/account/signup", json=one).status_code == 403
        first = alice.post("/api/account/signup", headers=headers, json=one)
        assert first.is_success, first.text
        code = first.json()["recovery_code"]
        cookie = first.headers["set-cookie"]
        assert "__Host-buddy_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie
        second = bob.post("/api/account/signup", headers=headers, json=two)
        assert second.is_success, second.text
        created = alice.post("/api/subjects", headers=headers, json=subject)
        assert created.is_success, created.text
        identifier = created.json()["id"]
        upload = alice.post(f"/api/subjects/{identifier}/uploads", headers=headers,
                            data={"tag": "notes"}, files={"file": ("private.txt", b"First student's private material")})
        assert upload.is_success, upload.text
        file_id = upload.json()["id"]
        chat = alice.post("/api/chats", headers=headers, json={})
        assert chat.is_success, chat.text
        chat_id = chat.json()["id"]
        provider = alice.post("/api/providers", headers=headers,
                              json={"kind": "Gemini", "model": "gemini-test", "key": "synthetic-test-provider-key"})
        assert provider.is_success, provider.text
        assert "synthetic-test-provider-key" not in provider.text
        assert "Custom" not in alice.get("/api/providers").json()["kinds"]

        assert bob.get("/api/subjects").json() == []
        assert bob.get(f"/api/uploads/{file_id}").status_code == 404
        assert bob.get(f"/api/uploads/{file_id}/pages/1").status_code == 404
        assert bob.get(f"/api/uploads/{file_id}/download").status_code == 404
        assert bob.get(f"/api/chats/{chat_id}").status_code == 404
        assert bob.get("/api/providers").json()["providers"] == []
        assert bob.put(f"/api/subjects/{identifier}", headers=headers, json=subject).status_code == 404
        assert bob.put(f"/api/subjects/{identifier}", headers={"Origin": "https://other.example.test"},
                       json=subject).status_code == 403
        assert alice.get(f"/api/uploads/{file_id}/download").content == b"First student's private material"

        sample = alice.post("/api/sample", headers=headers).json()
        job = alice.post(f"/api/subjects/{sample['id']}/notes/jobs", headers=headers,
                         json={"module": "1", "topic": "Stacks and queues"}).json()
        for _ in range(30):
            progress = alice.get(f"/api/jobs/{job['id']}").json()
            if progress["state"] in {"done", "failed"}:
                break
            time.sleep(.15)
        assert progress["state"] == "done", progress
        assert bob.get(f"/api/jobs/{job['id']}").status_code == 404

        old_session = alice.cookies.get("__Host-buddy_session")
        changed = {**one, "password": "replacement passphrase", "recovery_code": code}
        assert alice.post("/api/account/recover", headers=headers, json=changed).is_success
        previous = TestClient(app, base_url=origin, cookies={"__Host-buddy_session": old_session})
        assert previous.get("/api/subjects").status_code == 401
        previous.close()
        assert alice.post("/api/account/recover", headers=headers, json=changed).status_code == 401
        assert alice.post("/api/account/logout", headers=headers, json={}).is_success
        assert alice.get("/api/subjects").status_code == 401
        assert alice.post("/api/account/login", headers=headers, json=one).status_code == 401
        assert alice.post("/api/account/login", headers=headers,
                          json={"username": one["username"], "password": changed["password"]}).is_success
        assert alice.get(f"/api/uploads/{file_id}/download").is_success
    bob.close()


if __name__ == "__main__":
    run_hosted_scenario()
