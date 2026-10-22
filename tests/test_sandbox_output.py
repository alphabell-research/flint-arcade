"""A noisy checker cannot block on full pipes or accumulate an unlimited log."""
import asyncio
import sys

from arcade.sandbox import _bounded_output


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
