"""HTTP API, private build queue and isolated game delivery."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import html as html_escape
import hmac
from html.parser import HTMLParser
import ipaddress
import logging
import os
from pathlib import Path
import re
import secrets
import time
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .settings import Settings
from .storage import QueueError, Storage

log = logging.getLogger("arcade")
COOKIE = "arcade_owner"
GAME_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src 'none'; connect-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; sandbox allow-scripts"
SITE_CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
PUBLIC_HTML_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; worker-src 'none'; frame-src 'none'; child-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
PUBLIC_HTML_CSP_META = '<meta http-equiv="Content-Security-Policy" content="' + PUBLIC_HTML_CSP + '">'
PUBLIC_JOB_FIELDS = ("id", "status", "stage", "message", "game_id", "error")
PUBLIC_GAME_FIELDS = ("id", "title", "description", "created_at", "parent_id", "model_label")


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: Annotated[str, Field(min_length=1, max_length=1800)]
    dataset_opt_in: bool = False
    parent_id: Annotated[str | None, Field(max_length=80, pattern=r"^[A-Za-z0-9_-]+$")] = None


class _LeadingHead(HTMLParser):
    """Find a normal document head before any potentially executable content."""
    def __init__(self, document):
        super().__init__()
        self.insertion_offset = None
        self.unsafe_prefix = False
        self.lines = document.splitlines(keepends=True)
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        if self.insertion_offset is not None:
            return
        if tag == "head" and not self.unsafe_prefix:
            line, column = self.getpos()
            self.insertion_offset = sum(len(value) for value in self.lines[:line - 1]) + column + len(self.get_starttag_text())
        elif tag != "html":
            self.unsafe_prefix = True

    def handle_data(self, data):
        if self.insertion_offset is None and data.strip():
            self.unsafe_prefix = True


def prepare_public_html(document):
    """Add the same standalone network policy to every public game document.

    The original candidate and its provenance hashes remain unchanged in storage.
    """
    offset = _LeadingHead(document).insertion_offset
    if offset is None:
        doctype = re.match(r"\A\ufeff?\s*<!doctype\b[^>]*>", document, flags=re.I)
        offset = doctype.end() if doctype else 0
    marker = "\n" + PUBLIC_HTML_CSP_META + "\n"
    if document[offset:].startswith(marker):
        return document
    return document[:offset] + marker + document[offset:]


class _HeadTag(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.attributes = {}
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.attributes = {key.lower(): value or "" for key, value in attrs}


def _game_share_metadata(content, game, settings, has_preview):
    """Render crawler-readable share cards from public fields and cached images."""
    title = re.sub(r"\s+", " ", str(game["title"])).strip() or "Your game"
    description = re.sub(r"\s+", " ", str(game["description"])).strip() or "Play and remix this game in alphaBell Flint Arcade."
    page_title = title + " · Flint Arcade"
    metadata = {
        "description": description,
        "og:title": page_title,
        "og:description": description,
        "og:type": "website",
        "og:site_name": "Flint Arcade by alphaBell",
        "twitter:title": page_title,
        "twitter:description": description,
        "twitter:card": "summary",
    }
    canonical = None
    origin = urlsplit(settings.public_origin)
    if origin.scheme == "https" and origin.hostname and origin.username is None and origin.password is None:
        public_base = "https://" + origin.netloc + settings.root_path
        canonical = public_base + "/g/" + quote(game["id"], safe="")
        metadata["og:url"] = canonical
        if has_preview:
            image = public_base + "/preview/" + quote(game["id"], safe="") + ".png"
            metadata.update({"og:image": image, "og:image:type": "image/png", "og:image:alt": title + " game screenshot", "twitter:image": image, "twitter:image:alt": title + " game screenshot", "twitter:card": "summary_large_image"})
    # Remove the static card before inserting game-specific tags, avoiding
    # ambiguous duplicate metadata for crawlers. Keep unrelated head elements.
    replaced_keys = {"description", "og:title", "og:description", "og:type", "og:site_name", "og:url", "og:image", "og:image:type", "og:image:alt", "og:image:width", "og:image:height", "twitter:title", "twitter:description", "twitter:card", "twitter:image", "twitter:image:alt"}
    def retain_meta(match):
        attrs = _HeadTag(match.group(0)).attributes
        name = attrs.get("name", attrs.get("property", "")).lower()
        return "" if name in replaced_keys else match.group(0)
    tag_body = r'''(?:[^>"']|"[^"]*"|'[^']*')*'''
    content = re.sub(r"<meta\b" + tag_body + r">", retain_meta, content, flags=re.I)
    content = re.sub(r"<link\b" + tag_body + r">", lambda match: "" if "canonical" in _HeadTag(match.group(0)).attributes.get("rel", "").lower().split() else match.group(0), content, flags=re.I)
    content = re.sub(r"<title\b[^>]*>[\s\S]*?</title\s*>", "", content, flags=re.I)
    escaped = html_escape.escape
    tags = ["<title>" + escaped(page_title) + "</title>"]
    for name, value in metadata.items():
        attribute = "property" if name.startswith("og:") else "name"
        tags.append(f'<meta {attribute}="{name}" content="{escaped(value, quote=True)}">')
    if canonical:
        tags.append(f'<link rel="canonical" href="{escaped(canonical, quote=True)}">')
    return re.sub(r"</head\s*>", lambda match: "\n" + "\n".join(tags) + "\n" + match.group(0), content, count=1, flags=re.I)


def _key(data_dir: Path):
    path = data_dir / "session-signing-key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    key = secrets.token_bytes(32)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _signed_owner(owner, key):
    return owner + "." + hmac.new(key, owner.encode(), hashlib.sha256).hexdigest()


def _owner(request, key):
    value = request.cookies.get(COOKIE, "")
    owner, _, signature = value.partition(".")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}", owner):
        return None
    if not hmac.compare_digest(signature, hmac.new(key, owner.encode(), hashlib.sha256).hexdigest()):
        return None
    return owner


def _identity(request, settings, key):
    names = ("x-arcade-edge-timestamp", "x-arcade-edge-client", "x-arcade-edge-signature")
    if any(name in request.headers for name in names):
        timestamp, client_hash, signature = (request.headers.get(name, "") for name in names)
        if not settings.edge_secret or not re.fullmatch(r"[0-9]{1,12}", timestamp) or not re.fullmatch(r"[0-9a-f]{64}", client_hash) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise HTTPException(403, "Invalid edge identity.")
        if abs(time.time() - int(timestamp)) > 60:
            raise HTTPException(403, "Invalid edge identity.")
        expected = hmac.new(settings.edge_secret.encode(), (timestamp + "\n" + client_hash).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(403, "Invalid edge identity.")
        return hmac.new(key, ("edge:" + client_hash).encode(), hashlib.sha256).hexdigest()
    peer = request.client.host if request.client else "unknown"
    address = peer
    if settings.trusted_proxy_hops:
        try:
            trusted = any(ipaddress.ip_address(peer) in ipaddress.ip_network(cidr) for cidr in settings.trusted_proxy_cidrs)
            forwarded = [str(ipaddress.ip_address(part.strip())) for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
            if trusted and len(forwarded) >= settings.trusted_proxy_hops:
                address = forwarded[-settings.trusted_proxy_hops]
        except ValueError:
            pass
    return hmac.new(key, ("ip:" + address).encode(), hashlib.sha256).hexdigest()


async def _worker(app):
    settings, storage = app.state.settings, app.state.storage
    while True:
        if not settings.generation_enabled:
            await asyncio.sleep(settings.poll_seconds)
            continue
        job = await run_in_threadpool(storage.claim, settings.max_attempts)
        if job is None:
            await asyncio.sleep(settings.poll_seconds)
            continue
        try:
            parent = await run_in_threadpool(storage.game, job["parent_id"]) if job["parent_id"] else None
            async def stage(name, message=None):
                # Messages from providers are deliberately not returned to browsers.
                await run_in_threadpool(storage.stage, job["id"], name)
            generator = app.state.generator
            if generator is None:
                from .generation import generate
                generator = generate
            result = await asyncio.wait_for(generator(job["prompt"], parent["html"] if parent else None, stage, settings), timeout=settings.job_timeout)
            if not isinstance(result, dict) or result.get("qa", {}).get("accepted") is not True:
                raise ValueError("Game did not pass checks")
            html = result.get("html")
            if not isinstance(html, str) or not html.strip() or len(html.encode()) > settings.max_html_bytes:
                raise ValueError("Invalid game document")
            game_id = await run_in_threadpool(storage.publish, job, result, settings.model_label)
            try:
                await run_in_threadpool(storage.publish_preview, game_id, result)
            except OSError:
                # An optional preview must not change a successfully published job.
                log.warning("Preview for game %s was unavailable", game_id)
        except asyncio.CancelledError:
            # Keep the durable active record for bounded recovery on the next startup.
            raise
        except Exception as exc:
            # Provider URLs, keys and full exceptions must never reach the API or logs.
            log.warning("Build %s failed (%s)", job["id"], type(exc).__name__)
            await run_in_threadpool(storage.fail, job["id"])


def create_app(settings=None, generator=None):
    settings = settings or Settings()
    storage = Storage(settings.data_dir)
    key = _key(settings.data_dir)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(storage.recover, settings.max_attempts)
        task = asyncio.create_task(_worker(app)) if settings.worker_enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="alphaBell Flint Arcade", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan, root_path=settings.root_path)
    app.state.settings, app.state.storage, app.state.generator = settings, storage, generator

    @app.middleware("http")
    async def headers(request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 16_384):
                return JSONResponse({"detail": "Request is too large."}, status_code=413)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16_384:
                    return JSONResponse({"detail": "Request is too large."}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
            # Reject cross-origin writes, including drive-by form requests.
            origin = request.headers.get("origin")
            base = urlsplit(settings.public_origin or str(request.base_url))
            expected_origin = f"{base.scheme}://{base.netloc}"
            if origin and origin.rstrip("/") != expected_origin:
                return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
            try:
                request.state.rate_identity = _identity(request, settings, key)
            except HTTPException:
                return JSONResponse({"detail": "Invalid edge identity."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        response.headers.setdefault("Content-Security-Policy", SITE_CSP.replace("frame-ancestors 'none'", "frame-ancestors " + settings.frame_ancestors))
        request_path = request.url.path
        if settings.root_path and request_path.startswith(settings.root_path + "/"):
            request_path = request_path[len(settings.root_path):]
        if request_path.startswith("/api/jobs"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config")
    async def config():
        return {"model_label": settings.model_label, "generation_available": settings.generation_enabled, "source_url": settings.source_url, "huggingface_url": settings.huggingface_url}

    @app.get("/api/games")
    async def games():
        items = await run_in_threadpool(storage.games)
        return {"games": [public_game(item) for item in items]}

    def public_game(item):
        result = {name: item[name] for name in PUBLIC_GAME_FIELDS}
        if storage.preview_path(item["id"]):
            result["thumbnail"] = "preview/" + item["id"] + ".png"
        return result

    async def get_game(game_id):
        game = await run_in_threadpool(storage.game, game_id)
        if game is None:
            raise HTTPException(404, "Game not found.")
        return game

    @app.get("/api/games/{game_id}")
    async def game(game_id: str):
        item = await get_game(game_id)
        return {"game": public_game(item), "html": prepare_public_html(item["html"])}

    @app.post("/api/jobs", status_code=202)
    async def enqueue(body: BuildRequest, request: Request):
        if not settings.generation_enabled:
            raise HTTPException(503, "Game generation is being prepared. Please enjoy the gallery.")
        prompt = body.prompt.strip()
        if not prompt:
            raise HTTPException(422, "Describe a game to build.")
        owner = _owner(request, key) or secrets.token_urlsafe(24)
        try:
            job_id = await run_in_threadpool(storage.enqueue, owner, prompt, body.parent_id, request.state.rate_identity, settings.per_hour, settings.queue_cap, body.dataset_opt_in, settings.daily_cap)
        except QueueError as exc:
            raise HTTPException(exc.status, exc.message) from None
        response = JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)
        response.set_cookie(COOKIE, _signed_owner(owner, key), httponly=True, secure=settings.secure_cookie, samesite=settings.cookie_samesite, max_age=365 * 86400, path=settings.root_path or "/")
        if settings.cookie_samesite == "none":
            # CHIPS permits scoped ownership inside a Hugging Face iframe.
            response.headers["set-cookie"] += "; Partitioned"
        return response

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str, request: Request):
        owner = _owner(request, key)
        item = await run_in_threadpool(storage.job, job_id, owner) if owner else None
        if item is None:
            raise HTTPException(404, "Build not found.")
        return {name: item[name] for name in PUBLIC_JOB_FIELDS if item[name] is not None}

    @app.get("/play/{game_id}")
    async def play(game_id: str):
        item = await get_game(game_id)
        return HTMLResponse(prepare_public_html(item["html"]), headers={"Content-Security-Policy": GAME_CSP.replace("frame-ancestors 'self'", "frame-ancestors " + settings.frame_ancestors), "Cache-Control": "public, max-age=300"})

    @app.get("/download/{game_id}")
    async def download(game_id: str):
        item = await get_game(game_id)
        filename = re.sub(r"[^a-z0-9]+", "-", item["title"].lower()).strip("-")[:60] or "arcade-game"
        return Response(prepare_public_html(item["html"]), media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{filename}.html"', "Content-Security-Policy": GAME_CSP.replace("frame-ancestors 'self'", "frame-ancestors " + settings.frame_ancestors)})

    @app.get("/preview/{game_id}.png")
    async def preview(game_id: str):
        await get_game(game_id)
        image = await run_in_threadpool(storage.preview_path, game_id)
        if image is None:
            raise HTTPException(404, "Preview not found.")
        return FileResponse(image, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/")
    @app.get("/g/{game_id}")
    async def home(game_id: str | None = None):
        item = await get_game(game_id) if game_id else None
        index = settings.web_dir / "index.html"
        if not index.is_file():
            raise HTTPException(503, "The arcade is being prepared.")
        content = index.read_text(encoding="utf-8")
        if item:
            has_preview = await run_in_threadpool(storage.preview_path, game_id)
            content = _game_share_metadata(content, item, settings, has_preview is not None)
        prefix = settings.root_path + "/"
        if "<base " not in content.lower():
            content = re.sub(r"<head([^>]*)>", lambda match: match.group(0) + f'<base href="{html_escape.escape(prefix, quote=True)}">', content, count=1, flags=re.I)
        return HTMLResponse(content)

    @app.get("/{asset:path}")
    async def assets(asset: str):
        # Only packaged frontend assets, never arbitrary files or source documents.
        path = (settings.web_dir / asset).resolve()
        root = settings.web_dir.resolve()
        if any(part.startswith(".") for part in Path(asset).parts) or path.suffix.lower() not in {".js", ".css", ".svg", ".png", ".webp", ".ico", ".woff2"} or not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(path)

    return app
