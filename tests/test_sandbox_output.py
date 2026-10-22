"""A noisy checker cannot block on full pipes or accumulate an unlimited log."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from arcade.sandbox import _bounded_output, sandbox_command


def test_large_stdout_and_stderr_are_drained_with_bounded_tails():
    async def exercise():
        code = "import os; [(os.write(1,b'a'*8192),os.write(2,b'b'*8192)) for _ in range(1024)]; os.write(1,b'OUT-END'); os.write(2,b'ERR-END')"
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        readers = [asyncio.create_task(_bounded_output(stream)) for stream in (process.stdout, process.stderr)]
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
            stdout, stderr = await asyncio.gather(*readers)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
        assert process.returncode == 0
        assert len(stdout) == len(stderr) == 16_000
        assert stdout.endswith(b"OUT-END") and stderr.endswith(b"ERR-END")
    asyncio.run(exercise())


def test_portable_bubblewrap_child_has_only_explicit_environment(tmp_path, monkeypatch):
    browser = next((path for path in ("/usr/lib/chromium/chromium", "/opt/google/chrome/chrome") if Path(path).is_file()), None)
    if not browser or not shutil.which("bwrap"):
        pytest.skip("Bubblewrap and system Chromium are needed for the isolation probe")
    monkeypatch.setenv("ARCADE_SECRET_CANARY", "must-not-enter-worker")
    source, output = tmp_path / "game.html", tmp_path / "output"
    source.write_text("<!doctype html><html></html>")
    output.mkdir()
    command = sandbox_command(source, output, browser)
    command[-3:] = [sys.executable, "-c", "import json,os; print(json.dumps(dict(os.environ)))"]
    completed = subprocess.run(command, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=10, check=True)
    environment = json.loads(completed.stdout)
    assert environment["HOME"] == "/home/browser"
    assert environment["PATH"] == "/usr/bin"
    assert environment["LANG"] == "C.UTF-8"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "ARCADE_SECRET_CANARY" not in environment
    assert set(environment) <= {"HOME", "PATH", "LANG", "PYTHONDONTWRITEBYTECODE", "LC_CTYPE", "PWD"}
