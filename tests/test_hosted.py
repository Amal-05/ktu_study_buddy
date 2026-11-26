import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def hosted_run(tmp_path, database="", *args):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "BUDDY_HOSTED": "1", "BUDDY_DATA": str(tmp_path),
           "BUDDY_PUBLIC_ORIGIN": "https://study.example.test", "BUDDY_OPEN_SIGNUP": "1",
           "BUDDY_MAX_ACCOUNTS": "4", "BUDDY_EMBEDDINGS": "lexical", "PYTHONPATH": str(root),
           "BUDDY_DATABASE_URL": database, "BUDDY_PROVIDER_SECRET": "A test-only provider encryption secret for PostgreSQL"}
    result = subprocess.run([sys.executable, __file__, *args], env=env, cwd=root, capture_output=True, text=True, timeout=75)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hosted_isolation_and_recovery(tmp_path):
    hosted_run(tmp_path)


def test_oauth_sign_in_and_linking(tmp_path):
    hosted_run(tmp_path, "", "--verify-oauth")


def test_postgres_survives_fresh_filesystem(tmp_path):
    database = os.getenv("BUDDY_TEST_POSTGRES_URL")
    if not database:
        pytest.skip("Set BUDDY_TEST_POSTGRES_URL to an empty database")
    hosted_run(tmp_path / "first", database)
    hosted_run(tmp_path / "second", database, "--verify-restart")
    hosted_run(tmp_path / "third", database, "--verify-oauth")


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
        for _ in range(70):
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


def verify_restart():
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from backend.accounts import workspace_ids
    from backend.db import Provider, Session, workspace
    from backend.providers import cipher
    from backend.server import app

    origin = "https://study.example.test"
    with TestClient(app, base_url=origin) as client:
        response = client.post("/api/account/login", headers={"Origin": origin},
                               json={"username": "student.one", "password": "replacement passphrase"})
        assert response.is_success
        assert client.get("/api/uploads/1/download").content == b"First student's private material"
        assert len(client.get("/api/subjects").json()) == 2
        for identity in workspace_ids():
            with workspace(identity), Session() as db:
                provider = db.scalar(select(Provider).where(Provider.kind == "Gemini"))
                if provider:
                    assert cipher().decrypt(provider.secret) == b"synthetic-test-provider-key"
                    break
        else:
            raise AssertionError("Provider credentials did not survive the restart")


def verify_oauth():
    from unittest.mock import patch
    from urllib.parse import parse_qs, urlsplit

    from fastapi.testclient import TestClient

    from backend.server import app

    origin = "https://study.example.test"
    headers = {"Origin": origin}
    root = "/api/account/oauth/"
    env = {f"BUDDY_{provider}_CLIENT_{field}": f"test-{provider}-{field}"
           for provider in ("GOOGLE", "GITHUB") for field in ("ID", "SECRET")}

    def begin(client, provider, link=False):
        if link:
            response = client.post(root + provider + "/link", headers=headers)
            assert response.is_success, response.text
            target = response.json()["url"]
        else:
            response = client.get(root + provider + "/start")
            assert response.status_code == 303
            target = response.headers["location"]
        query = parse_qs(urlsplit(target).query)
        assert query["redirect_uri"] == [origin + root + provider + "/callback"]
        assert query["code_challenge_method"] == ["S256"]
        return query["state"][0]

    def callback(client, provider, state, code="first"):
        return client.get(root + provider + "/callback", params={"state": state, "code": code}).headers["location"]

    with (patch.dict(os.environ, env),
          patch("backend.oauth.provider_identity", side_effect=lambda provider, code, flow: (code, "student")) as provider,
          TestClient(app, base_url=origin, follow_redirects=False) as alice,
          TestClient(app, base_url=origin, follow_redirects=False) as bob):
        assert alice.get("/api/account").json()["sign_in_providers"] == ["google", "github"]
        assert alice.post(root + "google/link", headers=headers).status_code == 401
        state = begin(alice, "google")
        assert callback(bob, "google", state) == "/?signin_error=expired"
        assert callback(alice, "github", state) == "/?signin_error=expired"
        provider.assert_not_called()
        state = begin(alice, "google")
        assert callback(alice, "google", state) == "/"
        first = alice.get("/api/account").json()
        assert first["authenticated"] and first["linked_providers"] == ["google"]
        assert callback(alice, "google", state) == "/?signin_error=expired"
        assert callback(bob, "github", begin(bob, "github")) == "/"
        assert bob.get("/api/account").json()["username"] != first["username"]
        assert alice.post(root + "github/link").status_code == 403
        state = begin(alice, "github", link=True)
        assert callback(alice, "github", state) == "/?signin_error=linked#settings"
        state = begin(alice, "github", link=True)
        assert callback(alice, "github", state, code="second") == "/?signin_connected=1#settings"
        assert set(alice.get("/api/account").json()["linked_providers"]) == {"google", "github"}
        assert alice.post("/api/account/logout", headers=headers).is_success
        assert callback(alice, "github", begin(alice, "github"), code="second") == "/"
        assert alice.get("/api/account").json()["username"] == first["username"]
        state = begin(bob, "google", link=True)
        assert bob.post("/api/account/logout", headers=headers).is_success
        assert callback(bob, "google", state, code="third") == "/?signin_error=expired#settings"


if __name__ == "__main__":
    if "--verify-restart" in sys.argv:
        verify_restart()
    elif "--verify-oauth" in sys.argv:
        verify_oauth()
    else:
        run_hosted_scenario()
