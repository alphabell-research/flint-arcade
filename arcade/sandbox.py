"""Static restrictions and OS-isolated browser checks for untrusted game documents.

Static checks are a policy filter, not a complete JavaScript security boundary.
The browser runs inside a separate network, process, mount and user namespace.
"""
import asyncio
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import sys


class GameRejected(ValueError):
    pass


class _Policy(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors = []

    def handle_starttag(self, tag, attrs):
        attrs = {name.lower(): (value or "") for name, value in attrs}
        if tag.lower() in {"base", "iframe", "frame", "frameset", "object", "embed", "form"}:
            self.errors.append(f"Element {tag} is not allowed")
        if tag.lower() == "meta" and attrs.get("http-equiv", "").lower().strip() == "refresh":
            self.errors.append("Automatic navigation is not allowed")
        for name, value in attrs.items():
            if name in {"src", "href", "poster", "action", "formaction", "data", "xlink:href"}:
                if value and not (value.startswith("#") or (tag.lower() == "img" and value.lower().startswith("data:image/"))):
                    self.errors.append("External or linked resources are not allowed")
            if name in {"srcset", "srcdoc", "ping", "download", "target"}:
                self.errors.append(f"Attribute {name} is not allowed")

    handle_startendtag = handle_starttag


def validate_html(document: str, max_bytes=1_500_000):
    if not isinstance(document, str) or len(document.encode()) > max_bytes:
        raise GameRejected("Game document exceeds its size limit")
    if not re.search(r"<html(?:\s|>)", document, re.I) or not re.search(r"</html\s*>", document, re.I):
        raise GameRejected("Return one complete HTML document")
    parser = _Policy()
    parser.feed(document)
    lower = document.lower()
    # Obvious network/navigation APIs are disallowed. Obfuscated JavaScript is
    # constrained by browser CSP plus the OS sandbox, not by this expression.
    forbidden = r"\b(?:fetch|xmlhttprequest|websocket|eventsource|importscripts|worker|sharedworker)\s*\(|\b(?:location|parent|top|opener)\s*(?:[.\[]|=)|\bwindow\s*\.\s*open\s*\(|\bnavigator\s*\.\s*(?:sendbeacon|serviceworker)|\bdocument\s*\.\s*(?:cookie|domain)|\b(?:localstorage|sessionstorage|indexeddb)\b|\bimport\s*\("
    if re.search(forbidden, document, re.I):
        parser.errors.append("Network, storage and navigation APIs are not allowed")
    if re.search(r"@import\b|url\s*\(\s*['\"]?\s*(?:https?:|//|file:)", document, re.I):
        parser.errors.append("Remote CSS resources are not allowed")
    if "window.arcade" not in lower:
        parser.errors.append("Provide the required window.arcade interface")
    if parser.errors:
        raise GameRejected("; ".join(dict.fromkeys(parser.errors))[:1200])
    return True


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (40, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (20_000_000, 20_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


async def _bounded_output(stream, limit=16_000):
    """Drain the pipe continuously while retaining only a bounded log tail."""
    captured = bytearray()
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return bytes(captured)
        captured.extend(chunk)
        if len(captured) > limit:
            del captured[:-limit]


def sandbox_command(input_path: Path, output_dir: Path, chromium: str):
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise GameRejected("The isolated browser checker is unavailable")
    browser = Path(chromium).resolve()
    if not browser.is_file() or not any(browser.is_relative_to(Path(root)) for root in ("/usr", "/opt/google/chrome")):
        raise GameRejected("The isolated browser executable is unavailable")
    venv = Path(sys.prefix).resolve()
    if venv == Path(sys.base_prefix).resolve():
        raise GameRejected("The browser checker requires an isolated Python environment")
    # check_game starts Bubblewrap with an explicit minimal environment. Avoid
    # --clearenv, which older supported distro versions do not implement.
    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--uid", "0", "--gid", "0"]
    for path in ("/usr", "/lib", "/lib64"):
        if Path(path).exists():
            command += ["--ro-bind", path, path]
    if browser.is_relative_to(Path("/opt/google/chrome")):
        command += ["--ro-bind", "/opt/google/chrome", "/opt/google/chrome"]
    command += ["--ro-bind", str(venv), str(venv), "--ro-bind", str(Path(__file__).with_name("browser_worker.py").resolve()), "/app/browser_worker.py", "--ro-bind", str(input_path.resolve()), "/input/game.html", "--bind", str(output_dir.resolve()), "/output", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home/browser", "--setenv", "HOME", "/home/browser", "--setenv", "PATH", "/usr/bin", "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--chdir", "/tmp", str(Path(sys.executable)), "/app/browser_worker.py", str(browser)]
    return command


async def check_game(document, directory, settings):
    validate_html(document, settings.max_html_bytes)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    input_path = directory / "game.html"
    input_path.write_text(document)
    output_dir = directory / "qa"
    output_dir.mkdir(mode=0o700, exist_ok=True)
    command = sandbox_command(input_path, output_dir, settings.chromium)
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, preexec_fn=_limits, env={"PATH": "/usr/bin:/bin"})
    output_tasks = [asyncio.create_task(_bounded_output(stream)) for stream in (process.stdout, process.stderr)]
    async def memory_watchdog():
        # The deployment service also has a hard cgroup MemoryMax. This measured
        # process-tree bound catches excessive use during standalone local QA.
        while process.returncode is None:
            pending, seen, rss = [process.pid], set(), 0
            while pending:
                pid = pending.pop()
                if pid in seen:
                    continue
                seen.add(pid)
                try:
                    memory = Path(f"/proc/{pid}/smaps_rollup").read_text()
                    rss += int(re.search(r"^Pss:\s+(\d+)", memory, re.M).group(1)) * 1024
                    pending += [int(value) for value in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
            if rss > 768 * 1024 * 1024:
                (directory / "memory-limit.json").write_text(json.dumps({"pss_bytes": rss, "processes": len(seen)}))
                process.kill()
                return
            await asyncio.sleep(.1)
    watcher = asyncio.create_task(memory_watchdog())
    try:
        await asyncio.wait_for(process.wait(), timeout=settings.qa_timeout)
        stdout, stderr = await asyncio.gather(*output_tasks)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    finally:
        for task in output_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*output_tasks, return_exceptions=True)
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
    (directory / "checker-log.txt").write_bytes((stdout + b"\n" + stderr)[-16_000:])
    result_file = output_dir / "result.json"
    if process.returncode != 0 or not result_file.is_file() or result_file.stat().st_size > 100_000:
        raise GameRejected("The isolated browser checker could not finish")
    try:
        result = json.loads(result_file.read_text())
    except (ValueError, OSError) as exc:
        raise GameRejected("The isolated browser checker returned invalid results") from exc
    if not isinstance(result, dict) or not isinstance(result.get("accepted"), bool):
        raise GameRejected("The isolated browser checker returned invalid results")
    return result


async def isolation_preflight(directory, settings):
    """Verify OS isolation setup before spending any model inference tokens."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = directory / "output"
    output.mkdir(mode=0o700, exist_ok=True)
    command = sandbox_command(Path(__file__).with_name("starter.html"), output, settings.chromium)
    command[-3:] = ["/usr/bin/true"]
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, preexec_fn=_limits, env={"PATH": "/usr/bin:/bin"})
    readers = [asyncio.create_task(_bounded_output(stream, 2000)) for stream in (process.stdout, process.stderr)]
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
        stdout, stderr = await asyncio.gather(*readers)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    finally:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    (directory / "isolation-log.txt").write_bytes((stdout + b"\n" + stderr)[-4000:])
    if process.returncode != 0:
        raise RuntimeError("The isolated game checker is unavailable; no model generation was attempted")
    (directory / "result.json").write_text(json.dumps({"ready": True, "scope": "OS user, network, process and mount namespace setup only"}))
    return True
