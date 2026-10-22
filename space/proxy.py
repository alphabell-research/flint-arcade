"""Small, bounded same-origin gateway for the alphaBell Arcade Space."""
import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
from http.cookies import SimpleCookie
import ipaddress
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import httpx

MAX_DOCUMENT = 1_500_000
MAX_ASSET = 500_000
EDGE_COOKIE = "arcade_edge"
ASSETS = {"/app.js", "/styles.css", "/favicon.svg"}
ASSET_ROUTES = re.compile(r"^/(?:preview/[A-Za-z0-9_-]{1,80}\.png|fonts/[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.(?:woff2|txt))$")
ROUTES = re.compile(r"^/(?:g/[A-Za-z0-9_-]{1,80}|play/[A-Za-z0-9_-]{1,80}|download/[A-Za-z0-9_-]{1,80}|api/(?:config|games(?:/[A-Za-z0-9_-]{1,80})?|jobs(?:/[A-Za-z0-9_-]{1,80})?)|health)?$")


@dataclass(frozen=True)
class Settings:
    origin: str = "https://alphabell.com/arcade"
    edge_secret: str = ""
    space_host: str = ""
    per_hour: int = 3
    daily_cap: int = 30

    def __post_init__(self):
        parsed = urlsplit(self.origin)
        if parsed.scheme != "https" or parsed.netloc != "alphabell.com" or parsed.path.rstrip("/") != "/arcade" or parsed.query or parsed.fragment:
            raise ValueError("The gateway target must be the alphaBell Arcade application")
        if self.space_host and not re.fullmatch(r"[a-z0-9-]+\.hf\.space", self.space_host):
            raise ValueError("Invalid Space host")


class Limits:
    """A single-worker admission limit, in addition to durable backend quotas."""
    def __init__(self):
        self.hourly = defaultdict(deque)
        self.daily = deque()
        self.lock = asyncio.Lock()

    async def admit(self, cookie, client):
        now = time.time()
        async with self.lock:
            for key in list(self.hourly):
                values = self.hourly[key]
                while values and values[0] <= now - 3600:
                    values.popleft()
                if not values:
                    del self.hourly[key]
            while self.daily and self.daily[0] <= now - 86400:
                self.daily.popleft()
            if len(self.daily) >= self.daily_cap:
                return False
            keys = ("cookie:" + cookie, "client:" + client)
            if any(len(self.hourly[key]) >= self.per_hour for key in keys):
                return False
            for key in keys:
                self.hourly[key].append(now)
            self.daily.append(now)
            return True


def _signature(secret, value):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _edge_cookie(request, secret):
    value = request.cookies.get(EDGE_COOKIE, "")
    identity, _, signature = value.partition(".")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}", identity) or not hmac.compare_digest(signature, _signature(secret, "cookie\n" + identity)):
        identity = secrets.token_urlsafe(24)
    return identity, identity + "." + _signature(secret, "cookie\n" + identity)


def _client(request, settings):
    # HF's gateway appends the address it observes. Never trust the leftmost
    # element, which a caller can supply. Outside Spaces use the direct peer.
    address = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if settings.space_host and forwarded:
        address = forwarded.split(",")[-1].strip()
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        address = "unknown"
    return _signature(settings.edge_secret, "client-ip\n" + address)


def _upstream_cookie(raw):
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except Exception:
        return ""
    item = cookies.get("arcade_owner")
    if item and re.fullmatch(r"[A-Za-z0-9_-]{32}\.[0-9a-f]{64}", item.value):
        return "arcade_owner=" + item.value
    return ""


def _rewrite_cookie(raw):
    if not raw.lower().startswith("arcade_owner="):
        return None
    # Drop Domain and origin-specific Path; keep only this gateway's cookie.
    parts = [part.strip() for part in raw.split(";")]
    kept = [parts[0]]
    for part in parts[1:]:
        name = part.split("=", 1)[0].lower()
        if name in {"max-age", "expires"}:
            kept.append(part)
    return "; ".join(kept + ["Path=/", "HttpOnly", "Secure", "SameSite=None", "Partitioned"])


def _rewrite_location(location, origin):
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != "alphabell.com":
            return None
        path = parsed.path
    else:
        path = parsed.path
    prefix = urlsplit(origin).path.rstrip("/")
    if not (path == prefix or path.startswith(prefix + "/")):
        return None
    return (path[len(prefix):] or "/") + ("?" + parsed.query if parsed.query else "")


def create_app(settings=None, transport=None):
    settings = settings or Settings(origin=os.environ.get("ARCADE_ORIGIN", "https://alphabell.com/arcade").rstrip("/"), edge_secret=os.environ.get("ARCADE_EDGE_SECRET", ""), space_host=os.environ.get("SPACE_HOST", ""))
    client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, verify=True, limits=httpx.Limits(max_connections=12, max_keepalive_connections=4))
    limits = Limits()
    limits.per_hour, limits.daily_cap = settings.per_hour, settings.daily_cap

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST"])
    async def proxy(path, request: Request):
        public_path = "/" + path
        if settings.space_host and request.url.hostname != settings.space_host:
            return JSONResponse({"detail": "Not found."}, status_code=404)
        is_asset = public_path in ASSETS or bool(ASSET_ROUTES.fullmatch(public_path))
        if b"%" in request.scope.get("raw_path", b"") or request.url.query or (not is_asset and not ROUTES.fullmatch(public_path)):
            return JSONResponse({"detail": "Not found."}, status_code=404)
        is_post = request.method == "POST"
        if is_post and public_path != "/api/jobs":
            return JSONResponse({"detail": "Method not allowed."}, status_code=405)
        if not is_post and public_path == "/api/jobs":
            return JSONResponse({"detail": "Method not allowed."}, status_code=405)
        cookie = _upstream_cookie(request.headers.get("cookie", ""))
        # An explicit (possibly empty) Cookie header prevents the shared HTTP
        # client's upstream cookie jar from crossing browser ownership boundaries.
        outgoing = {"Accept": "*/*", "Accept-Encoding": "identity", "Cookie": cookie}
        body = b""
        edge_cookie = None
        if is_post:
            if not settings.edge_secret:
                return JSONResponse({"detail": "Generation is temporarily unavailable."}, status_code=503)
            incoming = request.headers.get("origin")
            expected_host = settings.space_host or request.url.netloc
            if incoming:
                parsed = urlsplit(incoming)
                if parsed.scheme not in {"https", "http"} or parsed.netloc != expected_host or parsed.path or parsed.query or parsed.fragment or (settings.space_host and parsed.scheme != "https"):
                    return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return JSONResponse({"detail": "A JSON request is required."}, status_code=415)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16_384:
                    return JSONResponse({"detail": "Request is too large."}, status_code=413)
                chunks.append(chunk)
            body = b"".join(chunks)
            identity, edge_cookie = _edge_cookie(request, settings.edge_secret)
            pseudonym = _client(request, settings)
            if not await limits.admit(identity, pseudonym):
                return JSONResponse({"detail": "The arcade build limit has been reached. Please try again later."}, status_code=429)
            timestamp = str(int(time.time()))
            outgoing.update({"Content-Type": "application/json", "Origin": "https://alphabell.com", "X-Arcade-Edge-Client": pseudonym, "X-Arcade-Edge-Timestamp": timestamp, "X-Arcade-Edge-Signature": _signature(settings.edge_secret, timestamp + "\n" + pseudonym)})
        target = settings.origin.rstrip("/") + public_path
        timeout = httpx.Timeout(20 if is_post else 35, connect=10)
        try:
            async with asyncio.timeout(20 if is_post else 35), client.stream(request.method, target, headers=outgoing, content=body, timeout=timeout) as upstream:
                if upstream.status_code >= 500:
                    return JSONResponse({"detail": "The arcade is temporarily unavailable. Please try again shortly."}, status_code=502)
                cap = MAX_ASSET if is_asset else MAX_DOCUMENT
                payload = bytearray()
                async for chunk in upstream.aiter_bytes():
                    if len(payload) + len(chunk) > cap:
                        return JSONResponse({"detail": "The arcade response is unavailable."}, status_code=502)
                    payload.extend(chunk)
                headers = {}
                for name in ("content-type", "content-disposition", "content-security-policy", "permissions-policy", "referrer-policy", "x-content-type-options"):
                    if name in upstream.headers:
                        headers[name] = upstream.headers[name]
                headers["Cache-Control"] = "no-store" if public_path.startswith("/api/jobs") or is_post else "public, max-age=60"
                if "location" in upstream.headers:
                    location = _rewrite_location(upstream.headers["location"], settings.origin)
                    if location is None:
                        return JSONResponse({"detail": "The arcade response is unavailable."}, status_code=502)
                    headers["Location"] = location
                if public_path == "/" or public_path.startswith("/g/"):
                    payload = bytes(payload).replace(b'<base href="/arcade/">', b'<base href="/">')
                response = Response(bytes(payload), status_code=upstream.status_code, headers=headers)
                for raw in upstream.headers.get_list("set-cookie"):
                    rewritten = _rewrite_cookie(raw)
                    if rewritten:
                        response.headers.append("set-cookie", rewritten)
                if edge_cookie:
                    response.headers.append("set-cookie", f"{EDGE_COOKIE}={edge_cookie}; Path=/; Max-Age=86400; HttpOnly; Secure; SameSite=None; Partitioned")
                return response
        except (httpx.HTTPError, ValueError, TimeoutError):
            return JSONResponse({"detail": "The arcade is temporarily unavailable. Please try again shortly."}, status_code=502)

    return app
