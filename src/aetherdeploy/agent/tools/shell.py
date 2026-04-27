from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from time import perf_counter

from ...observability import log_event


@dataclass
class ShellResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def run_async(cmd: list[str], cwd: str | None = None) -> ShellResult:
    """Ejecuta un comando asíncronamente."""
    start = perf_counter()
    log_event("tool.shell.async.start", command=cmd, cwd=cwd)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    result = ShellResult(
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )
    log_event(
        "tool.shell.async.end",
        command=cmd,
        cwd=cwd,
        returncode=result.returncode,
        duration_ms=round((perf_counter() - start) * 1000, 2),
        stdout=result.stdout,
        stderr=result.stderr,
    )
    return result


def run_sync(cmd: list[str], cwd: str | None = None) -> ShellResult:
    """Ejecuta un comando síncronamente."""
    start = perf_counter()
    log_event("tool.shell.sync.start", command=cmd, cwd=cwd)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    shell_result = ShellResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    log_event(
        "tool.shell.sync.end",
        command=cmd,
        cwd=cwd,
        returncode=shell_result.returncode,
        duration_ms=round((perf_counter() - start) * 1000, 2),
        stdout=shell_result.stdout,
        stderr=shell_result.stderr,
    )
    return shell_result
