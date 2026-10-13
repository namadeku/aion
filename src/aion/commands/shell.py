"""``shell`` actions: CMD / PowerShell without a window, with a timeout and a tree kill."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

from loguru import logger

ShellKind = Literal["cmd", "powershell"]
MAX_OUTPUT_BYTES = 64 << 10  # only the tail is kept: the work log shows a few lines anyway
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class ShellResult:
    code: int
    output: str


class ShellTimeout(Exception):
    pass


def _powershell_argv(command: str) -> list[str]:
    # -EncodedCommand: no quoting problems, whatever the script contains
    # no progress records: with stderr redirected they come out as CLIXML noise
    script = (
        "$ProgressPreference='SilentlyContinue';"
        f"[Console]::OutputEncoding=[Text.Encoding]::UTF8;{command}"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    exe = "powershell.exe" if sys.platform == "win32" else "pwsh"
    flags = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    return [exe, *flags, "-EncodedCommand", encoded]


def decode_output(data: bytes) -> str:
    """UTF-8 if it is, else the console (OEM) code page: ``cmd`` builtins write in it."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if sys.platform == "win32":
        import ctypes

        oem = int(ctypes.windll.kernel32.GetOEMCP())  # type: ignore[attr-defined]
        with contextlib.suppress(LookupError):
            return data.decode(f"cp{oem}", errors="replace")
    return data.decode("utf-8", errors="replace")


def kill_tree(pid: int) -> None:
    """Kill a process with all its children (``cmd /c start ...`` leaves grandchildren)."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=_NO_WINDOW,
        )
    else:
        import os
        import signal

        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)


async def run_shell(command: str, shell: ShellKind = "cmd", timeout: float = 60.0) -> ShellResult:
    """Run ``command`` and return its exit code and output (stdout and stderr together).

    Raises :class:`ShellTimeout` after ``timeout`` seconds. On timeout or cancellation the
    whole process tree is killed before the exception propagates.
    """
    common: dict[str, object] = {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
    }
    if sys.platform == "win32":
        common["creationflags"] = _NO_WINDOW
    else:
        common["start_new_session"] = True  # its own process group for kill_tree
    if shell == "powershell":
        proc = await asyncio.create_subprocess_exec(*_powershell_argv(command), **common)  # type: ignore[arg-type]
    else:
        proc = await asyncio.create_subprocess_shell(command, **common)  # type: ignore[arg-type]
    output = bytearray()

    async def collect() -> None:
        assert proc.stdout is not None
        while chunk := await proc.stdout.read(4096):
            output.extend(chunk)
            if len(output) > MAX_OUTPUT_BYTES:
                del output[: len(output) - MAX_OUTPUT_BYTES]
        await proc.wait()

    try:
        async with asyncio.timeout(timeout):
            await collect()
    except (TimeoutError, asyncio.CancelledError) as e:
        logger.info("Останавливаю процесс {} ({})", proc.pid, type(e).__name__)
        await asyncio.to_thread(kill_tree, proc.pid)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), 5)
        if isinstance(e, TimeoutError):
            raise ShellTimeout(f"не завершилась за {timeout:g} с") from e
        raise
    return ShellResult(proc.returncode or 0, decode_output(bytes(output)).strip())
