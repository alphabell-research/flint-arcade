from dataclasses import replace
from html.parser import HTMLParser

from fastapi.testclient import TestClient
import pytest

from arcade.app import create_app
from arcade.settings import Settings


class Head(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.meta = {}
        self.links = {}
        self.tags = []
        self.titles = []
        self.in_title = False
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.tags.append(tag)
        if tag == "meta":
            key = attrs.get("property", attrs.get("name"))
            self.meta.setdefault(key, []).append(attrs.get("content"))
        if tag == "link":
            self.links.setdefault(attrs.get("rel"), []).append(attrs.get("href"))
        if tag == "title":
            self.in_title = True

    def handle_data(self, data):
        if self.in_title:
            self.titles.append(data)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False


@pytest.fixture
def settings(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('''<!doctype html><html><head>
<meta charset="utf-8"><title>Flint Arcade</title>
<meta name="description" content="Static description">
<meta property="og:title" content="Static title">
<meta content="Static description" property="og:description">
<meta name='twitter:card' content='summary'>
<meta property="og:image" content="https://invalid.example/stale.png">
<link rel="canonical" href="https://invalid.example/stale">
<link rel="icon" href="./favicon.svg"><script defer src="./app.js"></script>
</head><body>Arcade</body></html>''')
    return Settings(data_dir=tmp_path / "data", web_dir=web, root_path="/arcade", public_origin="https://alphabell.com", worker_enabled=False)


def seed(app, title="Gravity Garden", description="Plant flowers & change gravity."):
    app.state.storage.import_game("garden", {"title": title, "description": description, "html": "<!doctype html><html><body>Play</body></html>", "qa": {"accepted": True}, "provenance": {"private_prompt": "do not disclose", "private_path": "/private/builds/secret"}})


def test_share_card_has_unique_game_metadata_and_real_canonical_preview(settings):
    app = create_app(settings)
    seed(app)
    app.state.storage.preview_dir.mkdir()
    (app.state.storage.preview_dir / "garden.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    with TestClient(app, base_url="https://internal.invalid") as client:
        response = client.get("/arcade/g/garden")
        assert response.status_code == 200
        head = Head(response.text)
        assert head.titles == ["Gravity Garden · Flint Arcade"]
        assert head.meta["description"] == ["Plant flowers & change gravity."]
        assert head.meta["og:title"] == ["Gravity Garden · Flint Arcade"]
        assert head.meta["og:description"] == ["Plant flowers & change gravity."]
        assert head.meta["og:url"] == ["https://alphabell.com/arcade/g/garden"]
        assert head.meta["og:image"] == ["https://alphabell.com/arcade/preview/garden.png"]
        assert head.meta["twitter:image"] == head.meta["og:image"]
        assert head.meta["twitter:card"] == ["summary_large_image"]
        assert head.meta["og:image:type"] == ["image/png"]
        assert head.links["canonical"] == head.meta["og:url"]
        assert head.links["icon"] == ["./favicon.svg"]
        assert '<base href="/arcade/">' in response.text
        assert "internal.invalid" not in response.text and "invalid.example" not in response.text
        assert "private_prompt" not in response.text and "do not disclose" not in response.text and "/private/builds" not in response.text
        assert client.get("/arcade/preview/garden.png").status_code == 200


def test_share_card_without_real_preview_omits_image_tags(settings):
    app = create_app(settings)
    seed(app)
    with TestClient(app) as client:
        head = Head(client.get("/arcade/g/garden").text)
        assert "og:image" not in head.meta and "twitter:image" not in head.meta
        assert head.meta["twitter:card"] == ["summary"]
        assert client.get("/arcade/preview/garden.png").status_code == 404
        assert client.get("/arcade/g/missing").status_code == 404
        assert Head(client.get("/arcade/").text).titles == ["Flint Arcade"]


def test_share_text_is_escaped_without_creating_markup_or_attributes(settings):
    title = 'Moon </title><script>alert("bad")</script>'
    description = '\" onload=\"alert(1)\"><img src=x onerror=alert(2)> & <strange>'
    app = create_app(settings)
    seed(app, title, description)
    with TestClient(app) as client:
        content = client.get("/arcade/g/garden").text
        head = Head(content)
        assert head.titles == [title + " · Flint Arcade"]
        assert head.meta["description"] == [description]
        assert head.meta["og:description"] == [description]
        assert head.tags.count("script") == 1
        assert "img" not in head.tags and "strange" not in head.tags
        assert "&lt;/title&gt;" in content and "&quot;" in content


@pytest.mark.parametrize("origin", ["", "https://user:password@alphabell.com", "http://internal.invalid"])
def test_canonical_cards_do_not_guess_request_hosts_or_leak_origin_credentials(settings, origin):
    app = create_app(replace(settings, public_origin=origin))
    seed(app)
    with TestClient(app, base_url="https://private-host.invalid") as client:
        content = client.get("/arcade/g/garden").text
        head = Head(content)
        assert "og:url" not in head.meta and "canonical" not in head.links
        assert "private-host.invalid" not in content and "password" not in content
