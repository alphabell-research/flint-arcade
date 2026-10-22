import asyncio
from dataclasses import replace
import json
import time

from fastapi.testclient import TestClient
import pytest

from arcade.app import create_app
from arcade.settings import Settings
from arcade.storage import Storage, QueueError


@pytest.fixture
def settings(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<!doctype html><html><head><title>Arcade</title></head><body></body></html>')
    (web / "app.js").write_text('console.log("alphaBell")')
    return Settings(data_dir=tmp_path / "data", web_dir=web, secure_cookie=False, generation_enabled=True, worker_enabled=False, poll_seconds=.01)


def accepted(**overrides):
    return dict({"title": "Tiny Moon", "description": "Jump across a moon.", "html": "<!doctype html><script>console.log('play')</script>", "qa": {"accepted": True}, "provenance": {"private_source": "not public"}}, **overrides)


def seed(app):
    app.state.storage.import_game("moon", accepted())


def test_disabled_has_no_model_configuration_leak(settings):
    with TestClient(create_app(replace(settings, generation_enabled=False, upstream_url="https://private.invalid", upstream_api_key="secret"))) as client:
        config = client.get("/api/config").json()
        assert config["generation_available"] is False
        assert "private" not in json.dumps(config) and "secret" not in json.dumps(config)
        assert client.post("/api/jobs", json={"prompt": "moon"}).status_code == 503


def test_job_ownership_and_signed_cookie(settings):
    app = create_app(settings)
    with TestClient(app) as owner, TestClient(app) as stranger:
        response = owner.post("/api/jobs", json={"prompt": "private prompt", "dataset_opt_in": True})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert owner.get(f"/api/jobs/{job_id}").status_code == 200
        assert stranger.get(f"/api/jobs/{job_id}").status_code == 404
        assert "private prompt" not in owner.get(f"/api/jobs/{job_id}").text
        assert app.state.storage.job(job_id)["dataset_opt_in"] == 1
        stranger.cookies.set("arcade_owner", owner.cookies.get("arcade_owner")[:-1] + "x")
        assert stranger.get(f"/api/jobs/{job_id}").status_code == 404
        assert "HttpOnly" in response.headers["set-cookie"]


def test_limits_persist_and_forwarded_header_cannot_spoof(settings):
    app = create_app(replace(settings, per_hour=1))
    with TestClient(app) as client:
        assert client.post("/api/jobs", json={"prompt": "first"}).status_code == 202
        assert client.post("/api/jobs", json={"prompt": "second"}, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 429
    with TestClient(create_app(replace(settings, per_hour=1))) as client:
        assert client.post("/api/jobs", json={"prompt": "third"}).status_code == 429


def test_queue_cap_parent_and_prompt_validation(settings):
    app = create_app(replace(settings, queue_cap=1))
    with TestClient(app) as client:
        for prompt in ["", " " * 10, "x" * 1801]:
            assert client.post("/api/jobs", json={"prompt": prompt}).status_code == 422
        assert client.post("/api/jobs", json={"prompt": "hi", "parent_id": "missing"}).status_code == 404
        seed(app)
        assert client.post("/api/jobs", json={"prompt": "remix", "parent_id": "moon"}).status_code == 202
        assert client.post("/api/jobs", json={"prompt": "queue full"}).status_code == 503
        assert len(app.state.storage.games()) == 1


def test_claim_is_globally_serial_and_recovery_is_bounded(settings):
    store = Storage(settings.data_dir)
    a = store.enqueue("owner", "a", None, "ip", 9, 9)
    b = store.enqueue("owner", "b", None, "ip", 9, 9)
    assert store.claim(2)["id"] == a
    assert store.claim(2) is None
    store.recover(2)
    assert store.claim(2)["id"] == a
    store.recover(2)
    assert store.job(a)["status"] == "failed"
    assert store.claim(2)["id"] == b


def test_public_game_routes_csp_download_and_no_private_data(settings):
    app = create_app(settings)
    seed(app)
    with TestClient(app) as client:
        game = client.get("/api/games/moon").json()
        assert set(game) == {"game", "html"}
        assert "private_source" not in json.dumps(game)
        play = client.get("/play/moon")
        assert "sandbox allow-scripts" in play.headers["content-security-policy"]
        assert "connect-src 'none'" in play.headers["content-security-policy"]
        assert "allow-same-origin" not in play.headers["content-security-policy"]
        assert "attachment" in client.get("/download/moon").headers["content-disposition"]
        assert client.get("/g/moon").status_code == 200
        assert client.get("/g/missing").status_code == 404
        assert client.get("/api/games").json()["games"][0]["id"] == "moon"


def test_source_files_and_dotfiles_are_never_served(settings):
    (settings.web_dir / ".secret.js").write_text("private")
    (settings.web_dir / "source.py").write_text("private")
    app = create_app(settings)
    with TestClient(app) as client:
        for path in ["/.secret.js", "/source.py", "/../arcade/app.py", "/%2e%2e/arcade/app.py", "/api/unknown", "/openapi.json"]:
            assert client.get(path).status_code == 404
        assert client.get("/app.js").status_code == 200


def test_prefix_origin_body_limit_and_embedding(settings):
    app = create_app(replace(settings, root_path="/arcade", public_origin="https://alphabell.com", frame_ancestors="'self' https://huggingface.co"))
    with TestClient(app, base_url="https://alphabell.com") as client:
        home = client.get("/arcade/")
        assert '<base href="/arcade/">' in home.text
        assert "https://huggingface.co" in home.headers["content-security-policy"]
        assert client.post("/arcade/api/jobs", json={"prompt": "a"}, headers={"Origin": "https://alphabell.com"}).status_code == 202
        assert client.post("/arcade/api/jobs", json={"prompt": "b"}, headers={"Origin": "https://other.invalid"}).status_code == 403
        assert client.post("/arcade/api/jobs", content=b"x" * 17000).status_code == 413
        assert client.post("/arcade/api/jobs", content=iter([b"x" * 10000, b"x" * 10000])).status_code == 413


def test_reject_unsafe_proxy_and_cookie_configuration(settings):
    with pytest.raises(ValueError):
        replace(settings, trusted_proxy_hops=1)
    with pytest.raises(ValueError):
        replace(settings, cookie_samesite="none", secure_cookie=False)


def wait_job(client, job_id):
    for _ in range(100):
        result = client.get(f"/api/jobs/{job_id}").json()
        if result["status"] in {"complete", "failed"}:
            return result
        time.sleep(.01)
    pytest.fail("worker did not complete")


def test_worker_publishes_only_accepted_and_passes_parent_html(settings):
    calls = []
    async def generate(prompt, parent_html, stage, config):
        calls.append((prompt, parent_html))
        await stage("testing", "private provider URL")
        return accepted()
    app = create_app(replace(settings, worker_enabled=True), generate)
    seed(app)
    with TestClient(app) as client:
        job_id = client.post("/api/jobs", json={"prompt": "Add bees", "parent_id": "moon"}).json()["job_id"]
        result = wait_job(client, job_id)
        assert result["status"] == "complete"
        assert "private provider" not in json.dumps(result)
        assert calls[0][1] == accepted()["html"]
        assert client.get(f'/api/games/{result["game_id"]}').json()["game"]["parent_id"] == "moon"


@pytest.mark.parametrize("mode", ["qa", "exception", "timeout", "html"])
def test_worker_failures_never_publish_or_leak(settings, mode):
    async def generate(prompt, parent_html, stage, config):
        if mode == "exception":
            raise RuntimeError("secret https://private.invalid")
        if mode == "timeout":
            await asyncio.sleep(1)
        if mode == "html":
            return accepted(html="")
        return accepted(qa={"accepted": False})
    app = create_app(replace(settings, worker_enabled=True, job_timeout=.05), generate)
    with TestClient(app) as client:
        job_id = client.post("/api/jobs", json={"prompt": "try"}).json()["job_id"]
        result = wait_job(client, job_id)
        assert result["status"] == "failed"
        assert "secret" not in json.dumps(result) and "private.invalid" not in json.dumps(result)
        assert client.get("/api/games").json() == {"games": []}


def test_import_rejects_unchecked_seed(settings):
    store = Storage(settings.data_dir)
    with pytest.raises(ValueError):
        store.import_game("bad", accepted(qa={"accepted": False}))
    with pytest.raises(ValueError):
        store.import_game("../bad", accepted())


def test_forwarded_chain_used_only_for_configured_trusted_peer(settings):
    trusted = replace(settings, per_hour=1, trusted_proxy_hops=1, trusted_proxy_cidrs=("127.0.0.1/32",))
    with TestClient(create_app(trusted), client=("127.0.0.1", 1234)) as client:
        assert client.post("/api/jobs", json={"prompt": "one"}, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 202
        assert client.post("/api/jobs", json={"prompt": "two"}, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 202
        assert client.post("/api/jobs", json={"prompt": "three"}, headers={"X-Forwarded-For": "192.0.2.99, 203.0.113.2"}).status_code == 429
    untrusted = replace(trusted, data_dir=settings.data_dir.parent / "untrusted")
    with TestClient(create_app(untrusted), client=("198.51.100.9", 1234)) as client:
        assert client.post("/api/jobs", json={"prompt": "one"}, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 202
        assert client.post("/api/jobs", json={"prompt": "two"}, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 429


def test_embedded_job_cookie_is_secure_partitioned_and_path_scoped(settings):
    app = create_app(replace(settings, root_path="/arcade", secure_cookie=True, cookie_samesite="none"))
    with TestClient(app, base_url="https://alphabell.com") as client:
        response = client.post("/arcade/api/jobs", json={"prompt":"moon"})
        assert response.status_code == 202
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "SameSite=none" in cookie and "Partitioned" in cookie
        assert "Path=/arcade" in cookie and "HttpOnly" in cookie
