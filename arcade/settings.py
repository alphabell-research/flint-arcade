"""Runtime configuration. Deployment-specific values stay outside source control."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import sys


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"true", "1", "yes"}


def _web_dir() -> Path:
    checkout = Path(__file__).resolve().parent.parent / "web"
    if (checkout / "index.html").is_file():
        return checkout
    return Path(sys.prefix) / "share" / "alphabell-arcade" / "web"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("ARCADE_DATA_DIR", str(Path.home() / ".local/share/alphabell-arcade"))))
    web_dir: Path = field(default_factory=_web_dir)
    generation_enabled: bool = field(default_factory=lambda: _bool("ARCADE_GENERATION_ENABLED"))
    root_path: str = field(default_factory=lambda: os.environ.get("ARCADE_ROOT_PATH", "").rstrip("/"))
    cookie_samesite: str = field(default_factory=lambda: os.environ.get("ARCADE_COOKIE_SAMESITE", "strict"))
    frame_ancestors: str = field(default_factory=lambda: os.environ.get("ARCADE_FRAME_ANCESTORS", "'self'"))
    public_origin: str = field(default_factory=lambda: os.environ.get("ARCADE_PUBLIC_ORIGIN", ""))
    model_label: str = "Flint 8-bit"
    source_url: str = field(default_factory=lambda: os.environ.get("ARCADE_SOURCE_URL", ""))
    huggingface_url: str = field(default_factory=lambda: os.environ.get("ARCADE_HUGGINGFACE_URL", ""))
    upstream_url: str = field(default_factory=lambda: os.environ.get("ARCADE_UPSTREAM_URL", ""))
    upstream_api_key: str = field(default_factory=lambda: os.environ.get("ARCADE_UPSTREAM_API_KEY", ""), repr=False)
    edge_secret: str = field(default_factory=lambda: os.environ.get("ARCADE_EDGE_SECRET", ""), repr=False)
    chromium: str = field(default_factory=lambda: os.environ.get("ARCADE_CHROMIUM", "/usr/lib/chromium/chromium" if Path("/usr/lib/chromium/chromium").is_file() else "/opt/google/chrome/chrome"))
    qa_timeout: float = field(default_factory=lambda: float(os.environ.get("ARCADE_QA_TIMEOUT", "45")))
    inference_timeout: float = field(default_factory=lambda: float(os.environ.get("ARCADE_INFERENCE_TIMEOUT", "180")))
    constrain_html: bool = field(default_factory=lambda: _bool("ARCADE_CONSTRAIN_HTML", True))
    completion_tokens: int = field(default_factory=lambda: int(os.environ.get("ARCADE_COMPLETION_TOKENS", "8192")))
    reasoning_budget: int | None = field(default_factory=lambda: int(os.environ["ARCADE_REASONING_BUDGET"]) if "ARCADE_REASONING_BUDGET" in os.environ else None)
    upstream_revision: str = field(default_factory=lambda: os.environ.get("ARCADE_UPSTREAM_REVISION", ""))
    upstream_model: str = field(default_factory=lambda: os.environ.get("ARCADE_UPSTREAM_MODEL", ""))
    trusted_proxy_hops: int = field(default_factory=lambda: int(os.environ.get("ARCADE_TRUSTED_PROXY_HOPS", "0")))
    trusted_proxy_cidrs: tuple[str, ...] = field(default_factory=lambda: tuple(filter(None, os.environ.get("ARCADE_TRUSTED_PROXY_CIDRS", "").split(","))))
    secure_cookie: bool = field(default_factory=lambda: _bool("ARCADE_SECURE_COOKIE", True))
    per_hour: int = field(default_factory=lambda: int(os.environ.get("ARCADE_PER_HOUR", "3")))
    daily_cap: int = field(default_factory=lambda: int(os.environ.get("ARCADE_DAILY_CAP", "100")))
    queue_cap: int = field(default_factory=lambda: int(os.environ.get("ARCADE_QUEUE_CAP", "8")))
    job_timeout: float = field(default_factory=lambda: float(os.environ.get("ARCADE_JOB_TIMEOUT", "600")))
    max_attempts: int = 2
    max_repairs: int = 4
    poll_seconds: float = 0.4
    worker_enabled: bool = True
    max_html_bytes: int = 1_500_000

    def __post_init__(self):
        if self.reasoning_budget is None:
            object.__setattr__(self, "reasoning_budget", 0 if self.constrain_html else 4096)
        if self.completion_tokens < 1 or self.reasoning_budget < 0:
            raise ValueError("Invalid model token limits")
        if not re.fullmatch(r"[A-Za-z0-9:/.*_' -]+", self.frame_ancestors):
            raise ValueError("Invalid frame ancestors")
        if self.root_path and (not self.root_path.startswith("/") or not re.fullmatch(r"/[A-Za-z0-9/_-]+", self.root_path)):
            raise ValueError("Invalid root path")
        if self.cookie_samesite not in {"strict", "lax", "none"} or (self.cookie_samesite == "none" and not self.secure_cookie):
            raise ValueError("Invalid cookie settings")
        if self.per_hour < 1 or self.daily_cap < 1 or self.queue_cap < 1 or self.job_timeout <= 0 or self.max_attempts < 1:
            raise ValueError("Invalid queue limits")
        if type(self.max_repairs) is not int or not 0 <= self.max_repairs <= 4:
            raise ValueError("Repair rounds must be between zero and four")
        if self.trusted_proxy_hops < 0:
            raise ValueError("Invalid trusted proxy hops")
        if self.trusted_proxy_hops and not self.trusted_proxy_cidrs:
            raise ValueError("Trusted proxy CIDRs are required when forwarded headers are enabled")
