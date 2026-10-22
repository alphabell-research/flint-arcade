from html.parser import HTMLParser
import json

from fastapi.testclient import TestClient
import pytest

from arcade.app import PUBLIC_HTML_CSP, PUBLIC_HTML_CSP_META, create_app, prepare_public_html
from arcade.settings import Settings


class Tags(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.tags = []
        self.feed(document)

    def handle_starttag(self, tag, attributes):
        self.tags.append((tag, dict(attributes)))


@pytest.mark.parametrize("document", [
    '<!doctype html><html><head><title>Moon</title></head><body><script>window.arcade={}</script></body></html>',
    '<!DOCTYPE html>\n<html lang="en">\n<head data-label="a > b">\n<script>window.arcade={}</script></head><body></body></html>',
    '<!doctype html><html><script>window.arcade={}</script><head></head><body></body></html>',
    '<!doctype html><!-- <head> fake -->\n<html><body><script>window.arcade={}</script></body></html>',
    '<html><script>const example="<head>";</script><body></body></html>',
    '<!doctype html><script>window.arcade={}</script>',
])
def test_public_document_policy_is_first_idempotent_and_does_not_rewrite_source(document):
    original = document
    prepared = prepare_public_html(document)
    assert document == original
    assert prepare_public_html(prepared) == prepared
    assert prepared.count(PUBLIC_HTML_CSP_META) == 1
    parsed = Tags(prepared)
    csp_index = next(i for i, (tag, attributes) in enumerate(parsed.tags) if tag == "meta" and attributes.get("http-equiv") == "Content-Security-Policy")
    script_index = next(i for i, (tag, _) in enumerate(parsed.tags) if tag == "script")
    assert csp_index < script_index
    policy = parsed.tags[csp_index][1]["content"]
    assert policy == PUBLIC_HTML_CSP
    for directive in ["default-src 'none'", "connect-src 'none'", "worker-src 'none'", "frame-src 'none'", "object-src 'none'", "base-uri 'none'", "form-action 'none'", "script-src 'unsafe-inline'", "style-src 'unsafe-inline'", "img-src data: blob:"]:
        assert directive in policy
    assert "frame-ancestors" not in policy and "sandbox" not in policy
    assert '<script>window.arcade={}</script>' not in document or '<script>window.arcade={}</script>' in prepared


def test_existing_stricter_policy_is_preserved():
    original = '<html><head><meta http-equiv="Content-Security-Policy" content="script-src \'none\'"></head><body></body></html>'
    prepared = prepare_public_html(original)
    assert "script-src 'none'" in prepared
    assert PUBLIC_HTML_CSP_META in prepared
    assert prepare_public_html(prepared) == prepared


def test_api_play_and_standalone_download_have_identical_prepared_bytes(tmp_path):
    original = '<!doctype html><html><head><title>Moon &amp; Bees</title></head><body><script>window.arcade={}</script></body></html>'
    provenance = {"html_sha256": "raw-source-proof", "private_path": "/private/builds/not-public"}
    settings = Settings(data_dir=tmp_path / "data", generation_enabled=False, worker_enabled=False)
    app = create_app(settings)
    app.state.storage.import_game("moon", {"title": 'Moon "><img src=x>', "description": 'A moon & bees.', "html": original, "qa": {"accepted": True}, "provenance": provenance})
    with TestClient(app) as client:
        api = client.get("/api/games/moon")
        play = client.get("/play/moon")
        download = client.get("/download/moon")
        assert api.status_code == play.status_code == download.status_code == 200
        assert api.json()["html"].encode() == play.content == download.content
        assert PUBLIC_HTML_CSP_META.encode() in download.content
        assert "attachment" in download.headers["content-disposition"]
        assert '<img' not in download.headers["content-disposition"]
        assert "sandbox allow-scripts" in play.headers["content-security-policy"]
        assert "frame-ancestors" in play.headers["content-security-policy"]
        assert "/private/builds" not in api.text
        stored = app.state.storage.game("moon")
        assert stored["html"] == original
        assert json.loads(stored["provenance"]) == provenance
