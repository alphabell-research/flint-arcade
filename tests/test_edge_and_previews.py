import base64
from dataclasses import replace
import hashlib
import hmac
import time

from fastapi.testclient import TestClient
import pytest

from arcade.app import create_app
from arcade.settings import Settings
from arcade.storage import Storage


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1kAAAAASUVORK5CYII=")
EDGE_SECRET = "a-test-only-edge-secret-that-is-not-a-deployment-key"


@pytest.fixture
def settings(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html><head></head><body>Arcade</body></html>")
    return Settings(data_dir=tmp_path / "data", web_dir=web, secure_cookie=False, generation_enabled=True, worker_enabled=False, edge_secret=EDGE_SECRET, public_origin="https://alphabell.com", per_hour=1, poll_seconds=.01)


def edge_headers(client_hash="a" * 64, timestamp=None, secret=EDGE_SECRET):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = hmac.new(secret.encode(), (timestamp + "\n" + client_hash).encode(), hashlib.sha256).hexdigest()
    return {"X-Arcade-Edge-Timestamp": timestamp, "X-Arcade-Edge-Client": client_hash, "X-Arcade-Edge-Signature": signature, "Origin": "https://alphabell.com"}


def accepted():
    return {"title": "Moon", "description": "A small moon.", "html": "<!doctype html><html><body>Play</body></html>", "qa": {"accepted": True}, "provenance": {"run_id": "trusted-run", "attempts": [{}]}}


def test_signed_edge_clients_have_separate_durable_hourly_limits(settings):
    app = create_app(settings)
    with TestClient(app, base_url="https://alphabell.com") as client:
        assert client.post("/api/jobs", json={"prompt": "first"}, headers=edge_headers()).status_code == 202
        assert client.post("/api/jobs", json={"prompt": "same client"}, headers=edge_headers()).status_code == 429
        assert client.post("/api/jobs", json={"prompt": "other client"}, headers=edge_headers("b" * 64)).status_code == 202
    with TestClient(create_app(settings), base_url="https://alphabell.com") as client:
        assert client.post("/api/jobs", json={"prompt": "after restart"}, headers=edge_headers()).status_code == 429
    with app.state.storage.connect() as db:
        identities = [row[0] for row in db.execute("SELECT identity FROM rate_events")]
    assert len(set(identities)) == 2
    assert "a" * 64 not in identities and "b" * 64 not in identities
    assert EDGE_SECRET not in repr(settings)


@pytest.mark.parametrize("mode", ["partial", "forged", "expired", "future", "uppercase", "no_secret"])
def test_forged_or_partial_edge_headers_fail_closed(settings, mode):
    headers = edge_headers()
    if mode == "partial":
        del headers["X-Arcade-Edge-Signature"]
    elif mode == "forged":
        headers["X-Arcade-Edge-Signature"] = "0" * 64
    elif mode == "expired":
        headers = edge_headers(timestamp=int(time.time()) - 65)
    elif mode == "future":
        headers = edge_headers(timestamp=int(time.time()) + 65)
    elif mode == "uppercase":
        headers = edge_headers("A" * 64)
    elif mode == "no_secret":
        settings = replace(settings, edge_secret="")
    with TestClient(create_app(settings), base_url="https://alphabell.com") as client:
        response = client.post("/api/jobs", json={"prompt": "not accepted"}, headers=headers)
        assert response.status_code == 403
        assert EDGE_SECRET not in response.text
        assert client.get("/api/games").json() == {"games": []}
        assert client.post("/api/jobs", json={"prompt": "ordinary unsigned request"}).status_code == 202


def test_canonical_origin_is_checked_before_edge_signature(settings):
    headers = edge_headers()
    headers["Origin"] = "https://unrelated.invalid"
    headers["X-Arcade-Edge-Signature"] = "wrong"
    with TestClient(create_app(settings), base_url="https://alphabell.com") as client:
        response = client.post("/api/jobs", json={"prompt": "reject"}, headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "Cross-origin requests are not allowed."


def test_global_daily_limit_survives_failed_jobs_and_restart(settings):
    settings = replace(settings, daily_cap=2, per_hour=10)
    app = create_app(settings)
    with TestClient(app) as client:
        for i in range(2):
            response = client.post("/api/jobs", json={"prompt": "build " + str(i)})
            assert response.status_code == 202
            app.state.storage.fail(response.json()["job_id"])
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.post("/api/jobs", json={"prompt": "over daily limit"}, headers=edge_headers()).status_code == 429
        with app.state.storage.connect() as db:
            db.execute("UPDATE jobs SET created_at=? WHERE id=(SELECT id FROM jobs LIMIT 1)", (time.time() - 86401,))
        assert client.post("/api/jobs", json={"prompt": "old job expired"}, headers=edge_headers()).status_code == 202
    with pytest.raises(ValueError):
        replace(settings, daily_cap=0)


def test_verified_preview_is_public_and_private_paths_are_not(settings):
    app = create_app(replace(settings, root_path="/arcade", frame_ancestors="'self' https://huggingface.co https://*.hf.space"))
    store = app.state.storage
    result = accepted()
    source = settings.data_dir / "builds/trusted-run/1/qa/desktop.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(PNG)
    store.import_game("moon", result)
    assert store.publish_preview("moon", result) is True
    with TestClient(app) as client:
        image = client.get("/arcade/preview/moon.png")
        assert image.status_code == 200 and image.content == PNG
        assert image.headers["content-type"] == "image/png"
        assert "max-age=" in image.headers["cache-control"]
        for url in ["/arcade/api/games", "/arcade/api/games/moon"]:
            response = client.get(url)
            assert "preview/moon.png" in response.text
            assert "trusted-run" not in response.text and str(settings.data_dir) not in response.text
        assert "https://huggingface.co" in client.get("/arcade/play/moon").headers["content-security-policy"]
        for url in ["/arcade/preview/missing.png", "/arcade/preview/%2e%2e.png", "/arcade/preview/%2e%2e%2Fmoon.png", "/arcade/builds/trusted-run/1/qa/desktop.png"]:
            assert client.get(url).status_code == 404
        store.preview_path("moon").unlink()
        assert "thumbnail" not in client.get("/arcade/api/games/moon").json()["game"]
        assert client.get("/arcade/preview/moon.png").status_code == 404
        response = client.post("/arcade/api/jobs", json={"prompt": "new moon"})
        assert response.headers["cache-control"] == "no-store"
        job_id = response.json()["job_id"]
        assert client.get("/arcade/api/jobs/" + job_id).headers["cache-control"] == "no-store"


def test_preview_copy_rejects_unaccepted_unsafe_and_symlink_paths(settings, tmp_path):
    store = Storage(settings.data_dir)
    result = accepted()
    source = settings.data_dir / "builds/trusted-run/1/qa/desktop.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(PNG)
    for run_id in ["../trusted-run", "/tmp/elsewhere", "bad/name"]:
        result["provenance"]["run_id"] = run_id
        assert store.publish_preview("moon", result) is False
    result = accepted()
    result["qa"]["accepted"] = False
    assert store.publish_preview("moon", result) is False
    result = accepted()
    source.unlink()
    private = tmp_path / "private.png"
    private.write_bytes(PNG)
    source.symlink_to(private)
    assert store.publish_preview("moon", result) is False
    store.preview_dir.mkdir()
    (store.preview_dir / "moon.png").symlink_to(private)
    assert store.preview_path("moon") is None


def test_worker_copies_accepted_preview_without_exposing_build_paths(settings):
    async def generate(prompt, parent_html, stage, config):
        source = config.data_dir / "builds/trusted-run/1/qa/desktop.png"
        source.parent.mkdir(parents=True)
        source.write_bytes(PNG)
        return accepted()
    app = create_app(replace(settings, worker_enabled=True), generate)
    with TestClient(app) as client:
        job_id = client.post("/api/jobs", json={"prompt": "a small moon"}).json()["job_id"]
        for _ in range(100):
            job = client.get("/api/jobs/" + job_id).json()
            if job.get("game_id") and client.get("/preview/" + job["game_id"] + ".png").status_code == 200:
                break
            time.sleep(.01)
        else:
            pytest.fail("worker did not publish its preview")
        assert client.get("/api/games").json()["games"][0]["thumbnail"] == "preview/" + job["game_id"] + ".png"
