import hashlib
import hmac
import importlib.util
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

_spec = importlib.util.spec_from_file_location("arcade_space_proxy", Path(__file__).resolve().parents[1] / "space/proxy.py")
_proxy = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _proxy
_spec.loader.exec_module(_proxy)
MAX_DOCUMENT, Settings, create_app = _proxy.MAX_DOCUMENT, _proxy.Settings, _proxy.create_app


SECRET = "test-only-edge-key"


def configured(**kwargs):
    return Settings(edge_secret=SECRET, space_host="alphabell-flint-arcade.hf.space", **kwargs)


def client_for(handler, settings=None):
    return TestClient(create_app(settings or configured(), transport=httpx.MockTransport(handler)), base_url="https://alphabell-flint-arcade.hf.space")


@pytest.mark.parametrize("path", ["https://alphabell-flint-arcade.hf.space//evil.invalid/", "https://evil.invalid/", "/%2f%2fevil.invalid/", "/.git/config", "/proxy.py", "/api/other", "/app.js?url=https://evil.invalid", "/api/games/%2e%2e", "/foo/../../private.env"])
def test_route_allowlist_rejects_untrusted_paths(path):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, text="unexpected")
    with client_for(handle) as client:
        assert client.get(path).status_code == 404
    assert not seen


def test_target_is_fixed_and_redirects_cannot_escape():
    with pytest.raises(ValueError):
        Settings(origin="https://evil.invalid/arcade")
    with pytest.raises(ValueError):
        Settings(origin="https://alphabell.com@evil.invalid/arcade")
    seen = []
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.invalid/game"})
    with client_for(handle) as client:
        assert client.get("/g/moon", follow_redirects=False).status_code == 502
    assert seen == ["https://alphabell.com/arcade/g/moon"]


def test_post_origin_and_content_type_and_streamed_size():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(202, json={"job_id": "moon"})
    with client_for(handle) as client:
        assert client.post("/api/jobs", json={"prompt": "moon"}, headers={"origin": "https://evil.invalid"}).status_code == 403
        assert client.post("/api/jobs", content="prompt=moon", headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415
        assert client.post("/api/jobs", content=iter([b"a" * 10000, b"b" * 10000]), headers={"content-type": "application/json"}).status_code == 413
        assert client.post("/api/games", json={}).status_code == 405
    assert not calls


def test_signing_is_private_and_client_ip_is_not_forwarded():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(202, json={"job_id": "moon"}, headers={"set-cookie": "arcade_owner=" + "a" * 32 + "." + "b" * 64 + "; Path=/arcade; SameSite=Strict; HttpOnly; Domain=alphabell.com"})
    with client_for(handle) as client:
        response = client.post("/api/jobs", json={"prompt": "moon"}, headers={"Origin": "https://alphabell-flint-arcade.hf.space", "X-Forwarded-For": "198.51.100.1, 203.0.113.8", "Authorization": "not-forwarded", "X-Arcade-Edge-Client": "spoof"})
        assert response.status_code == 202
        assert SECRET not in response.text
        assert SECRET not in str(response.headers)
        cookies = response.headers.get_list("set-cookie")
        assert any("arcade_owner=" in x and "Path=/;" in x and "SameSite=None" in x and "Partitioned" in x for x in cookies)
        assert all("Domain=" not in x and "Path=/arcade" not in x for x in cookies)
    request = seen[0]
    client_hash = request.headers["X-Arcade-Edge-Client"]
    timestamp = request.headers["X-Arcade-Edge-Timestamp"]
    expected_client = hmac.new(SECRET.encode(), b"client-ip\n203.0.113.8", hashlib.sha256).hexdigest()
    assert client_hash == expected_client
    expected = hmac.new(SECRET.encode(), (timestamp + "\n" + client_hash).encode(), hashlib.sha256).hexdigest()
    assert request.headers["X-Arcade-Edge-Signature"] == expected
    assert request.headers["origin"] == "https://alphabell.com"
    assert "authorization" not in request.headers and "x-forwarded-for" not in request.headers
    assert "203.0.113.8" not in str(request.headers)


def test_base_location_cookie_filter_and_private_no_store():
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path.endswith("/g/moon"):
            return httpx.Response(200, text='<base href="/arcade/"><h1>Play</h1>', headers={"content-type": "text/html", "content-security-policy": "base-uri 'self'; frame-ancestors 'self' https://huggingface.co"})
        if request.url.path.endswith("/api/jobs/moon"):
            return httpx.Response(200, json={"status": "queued"})
        return httpx.Response(302, headers={"location": "https://alphabell.com/arcade/g/moon"})
    with client_for(handle) as client:
        page = client.get("/g/moon")
        assert '<base href="/">' in page.text and "/arcade/" not in page.text
        assert "https://huggingface.co" in page.headers["content-security-policy"]
        assert client.get("/api/jobs/moon").headers["cache-control"] == "no-store"
        assert client.get("/", follow_redirects=False).headers["location"] == "/g/moon"
        owner = "a" * 32 + "." + "b" * 64
        client.get("/health", headers={"cookie": "private=secret; arcade_owner=" + owner}, follow_redirects=False)
    assert seen[-1].headers["cookie"] == "arcade_owner=" + owner


def test_quotas_and_missing_secret_fail_closed():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(202, json={"job_id": "moon"})
    with client_for(handle) as client:
        for _ in range(3):
            assert client.post("/api/jobs", json={"prompt": "moon"}).status_code == 202
        assert client.post("/api/jobs", json={"prompt": "moon"}).status_code == 429
    with client_for(handle, Settings()) as client:
        assert client.post("/api/jobs", json={"prompt": "moon"}).status_code == 503
    assert len(calls) == 3


def test_shared_http_client_never_reuses_another_visitors_owner_cookie():
    seen = []
    owner = "a" * 32 + "." + "b" * 64
    def handle(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "moon"}, headers={"set-cookie": "arcade_owner=" + owner + "; Path=/arcade; Secure"})
        return httpx.Response(404, json={"detail": "Build not found."})
    with client_for(handle) as client:
        assert client.post("/api/jobs", json={"prompt": "moon"}).status_code == 202
        # A separate visitor sends no cookie, despite the pooled upstream client
        # having observed the first response's Set-Cookie header.
        client.cookies.clear()
        assert client.get("/api/jobs/moon").status_code == 404
    assert seen[-1].headers.get("cookie", "") == ""


def test_bounds_and_upstream_errors_are_generic():
    def giant(request):
        return httpx.Response(200, content=b"x" * (MAX_DOCUMENT + 1))
    with client_for(giant) as client:
        assert client.get("/g/moon").status_code == 502
    def failure(request):
        return httpx.Response(500, text="private exception, credential and server path")
    with client_for(failure) as client:
        response = client.get("/api/config")
        assert response.status_code == 502 and "credential" not in response.text
    def network(request):
        raise httpx.ConnectError("secret private URL", request=request)
    with client_for(network) as client:
        response = client.get("/health")
        assert response.status_code == 502 and "secret" not in response.text


@pytest.mark.parametrize("path", ["/fonts/dm-sans.woff2", "/fonts/space-grotesk.woff2", "/fonts/OFL.txt", "/preview/moon_1.png"])
def test_known_fonts_and_previews_are_bounded_assets(path):
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"asset")
    with client_for(handle) as client:
        assert client.get(path).content == b"asset"
    assert calls == ["https://alphabell.com/arcade" + path]


@pytest.mark.parametrize("query", ["__theme=system", "__theme=light", "__theme=dark", "__theme=%73ystem"])
def test_hugging_face_theme_query_loads_root_without_forwarding_query(query):
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, text='<base href="/arcade/"><h1>Arcade</h1>')
    with client_for(handle) as client:
        response = client.get("/?" + query)
        assert response.status_code == 200
        assert '<base href="/">' in response.text
    assert calls == ["https://alphabell.com/arcade/"]


@pytest.mark.parametrize("path", ["/?__theme=system&__theme=dark", "/?__theme=system&url=https://evil.invalid", "/?__theme=other", "/?__theme=", "/?__theme", "/?%5f%5ftheme=system", "/?__theme=system;url=evil", "/api/config?__theme=system", "/g/moon?__theme=system"])
def test_theme_query_rejects_duplicates_unknown_values_and_nonroot_routes(path):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200)
    with client_for(handle) as client:
        assert client.get(path).status_code == 404
    assert not calls
