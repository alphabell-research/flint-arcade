"""Run with a separately installed wheel's Python, from outside the checkout."""
from importlib import metadata, resources
from pathlib import Path
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

import arcade
from arcade.app import create_app
from arcade.settings import Settings


class InstalledWheelSmoke(unittest.TestCase):
    def test_installed_app_serves_complete_assets_without_checkout(self):
        checkout = Path(__file__).resolve().parent.parent
        self.assertFalse(Path(arcade.__file__).resolve().is_relative_to(checkout))
        self.assertNotEqual(sys.prefix, sys.base_prefix)
        self.assertEqual(metadata.version("fastapi"), "0.159.1")
        self.assertEqual(metadata.version("uvicorn"), "0.54.0")
        self.assertEqual(metadata.version("httpx"), "0.28.1")
        starter = resources.files("arcade").joinpath("starter.html").read_text()
        self.assertIn("window.arcade", starter)
        with tempfile.TemporaryDirectory(prefix="alphabell-installed-smoke-") as directory:
            settings = Settings(data_dir=Path(directory), generation_enabled=False, worker_enabled=False, root_path="/arcade")
            self.assertEqual(settings.web_dir.resolve(), (Path(sys.prefix) / "share/alphabell-arcade/web").resolve())
            for asset in ("index.html", "app.js", "styles.css", "favicon.svg", "fonts/dm-sans.woff2", "fonts/space-grotesk.woff2", "fonts/DM-Sans-OFL.txt", "fonts/Space-Grotesk-OFL.txt"):
                self.assertGreater((settings.web_dir / asset).stat().st_size, 100)
            with TestClient(create_app(settings)) as client:
                home = client.get("/arcade/")
                self.assertEqual(home.status_code, 200)
                self.assertIn("Flint Arcade", home.text)
                self.assertIn('<base href="/arcade/">', home.text)
                for asset, content_type in (("app.js", "javascript"), ("styles.css", "text/css"), ("favicon.svg", "image/svg+xml"), ("fonts/dm-sans.woff2", "font/woff2"), ("fonts/space-grotesk.woff2", "font/woff2")):
                    response = client.get("/arcade/" + asset)
                    self.assertEqual(response.status_code, 200, asset)
                    self.assertIn(content_type, response.headers["content-type"])
                    self.assertGreater(len(response.content), 100)
                self.assertEqual(client.get("/arcade/api/games").json(), {"games": []})
                self.assertFalse(client.get("/arcade/api/config").json()["generation_available"])
                self.assertEqual(client.post("/arcade/api/jobs", json={"prompt": "A tiny moon garden"}).status_code, 503)


if __name__ == "__main__":
    unittest.main(verbosity=2)
