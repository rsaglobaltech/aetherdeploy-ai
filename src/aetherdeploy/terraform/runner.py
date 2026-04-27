from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable

from ..config import get_config
from ..observability import log_event


@dataclass
class TerraformResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class TerraformRunner:
    """Wrapper del CLI de Terraform con streaming de output."""

    def __init__(self, binary: str | None = None, timeout: int | None = None) -> None:
        self._bin = binary or get_config().terraform_binary
        self._timeout = timeout or int(os.environ.get("AETHER_TERRAFORM_TIMEOUT", "1800"))

    def init(self, workdir: Path, backend: bool = True) -> TerraformResult:
        args = ["init", "-no-color"]
        if not backend:
            args.append("-backend=false")
        return self._run(args, workdir)

    def validate(self, workdir: Path) -> TerraformResult:
        return self._run(["validate", "-no-color"], workdir)

    def plan(self, workdir: Path, vars: dict | None = None, refresh: bool = True) -> TerraformResult:
        args = ["plan", "-no-color"]
        if not refresh:
            args.append("-refresh=false")
        for k, v in (vars or {}).items():
            args += [f"-var={k}={v}"]
        return self._run(args, workdir)

    def apply(self, workdir: Path, vars: dict | None = None, auto_approve: bool = False) -> TerraformResult:
        args = ["apply", "-no-color"]
        if auto_approve:
            args.append("-auto-approve")
        for k, v in (vars or {}).items():
            args += [f"-var={k}={v}"]
        return self._run(args, workdir)

    def destroy(self, workdir: Path, vars: dict | None = None, auto_approve: bool = False) -> TerraformResult:
        args = ["destroy", "-no-color"]
        if auto_approve:
            args.append("-auto-approve")
        for k, v in (vars or {}).items():
            args += [f"-var={k}={v}"]
        return self._run(args, workdir)

    def output(self, workdir: Path) -> TerraformResult:
        return self._run(["output", "-json"], workdir)

    def is_available(self) -> bool:
        try:
            result = subprocess.run([self._bin, "version"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _run(self, args: list[str], workdir: Path) -> TerraformResult:
        cmd = [self._bin, *args]
        start = perf_counter()
        log_event("terraform.run.start", command=cmd, workdir=str(workdir), timeout=self._timeout)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(workdir),
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            tf_result = TerraformResult(
                returncode=124,
                stdout=exc.stdout or "",
                stderr=f"Terraform superó el timeout de {self._timeout}s.",
            )
            log_event(
                "terraform.run.timeout",
                level="ERROR",
                command=cmd,
                workdir=str(workdir),
                duration_ms=round((perf_counter() - start) * 1000, 2),
                stdout=tf_result.stdout,
                stderr=tf_result.stderr,
            )
            return tf_result
        tf_result = TerraformResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        log_event(
            "terraform.run.end",
            command=cmd,
            workdir=str(workdir),
            returncode=tf_result.returncode,
            duration_ms=round((perf_counter() - start) * 1000, 2),
            stdout=tf_result.stdout,
            stderr=tf_result.stderr,
        )
        return tf_result

    async def run_async(self, args: list[str], workdir: Path) -> TerraformResult:
        cmd = [self._bin, *args]
        start = perf_counter()
        log_event("terraform.async.start", command=cmd, workdir=str(workdir), timeout=self._timeout)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            tf_result = TerraformResult(
                returncode=124,
                stdout="",
                stderr=f"Terraform superó el timeout de {self._timeout}s.",
            )
            log_event(
                "terraform.async.timeout",
                level="ERROR",
                command=cmd,
                workdir=str(workdir),
                duration_ms=round((perf_counter() - start) * 1000, 2),
                stderr=tf_result.stderr,
            )
            return tf_result
        tf_result = TerraformResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
        log_event(
            "terraform.async.end",
            command=cmd,
            workdir=str(workdir),
            returncode=tf_result.returncode,
            duration_ms=round((perf_counter() - start) * 1000, 2),
            stdout=tf_result.stdout,
            stderr=tf_result.stderr,
        )
        return tf_result

    # ------------------------------------------------------------------
    # Streaming async — emite cada línea a un callback mientras ejecuta
    # ------------------------------------------------------------------

    async def init_streaming(
        self,
        workdir: Path,
        backend: bool = True,
        on_line: Callable[[str], None] | None = None,
    ) -> TerraformResult:
        args = ["init", "-no-color"]
        if not backend:
            args.append("-backend=false")
        return await self._run_streaming_async(args, workdir, on_line)

    async def plan_streaming(
        self,
        workdir: Path,
        vars: dict | None = None,
        refresh: bool = True,
        on_line: Callable[[str], None] | None = None,
    ) -> TerraformResult:
        args = ["plan", "-no-color"]
        if not refresh:
            args.append("-refresh=false")
        for k, v in (vars or {}).items():
            args += [f"-var={k}={v}"]
        return await self._run_streaming_async(args, workdir, on_line)

    async def apply_streaming(
        self,
        workdir: Path,
        vars: dict | None = None,
        auto_approve: bool = False,
        on_line: Callable[[str], None] | None = None,
    ) -> TerraformResult:
        args = ["apply", "-no-color"]
        if auto_approve:
            args.append("-auto-approve")
        for k, v in (vars or {}).items():
            args += [f"-var={k}={v}"]
        return await self._run_streaming_async(args, workdir, on_line)

    async def destroy_streaming(
        self,
        workdir: Path,
        vars: dict | None = None,
        auto_approve: bool = False,
        on_line: Callable[[str], None] | None = None,
    ) -> TerraformResult:
        args = ["destroy", "-no-color"]
        if auto_approve:
            args.append("-auto-approve")
        for k, v in (vars or {}).items():
            args += [f"-var={k}={v}"]
        return await self._run_streaming_async(args, workdir, on_line)

    async def _run_streaming_async(
        self,
        args: list[str],
        workdir: Path,
        on_line: Callable[[str], None] | None = None,
    ) -> TerraformResult:
        cmd = [self._bin, *args]
        start = perf_counter()
        log_event("terraform.streaming.start", command=cmd, workdir=str(workdir), timeout=self._timeout)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            start_new_session=True,
        )
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        async def _read(stream: asyncio.StreamReader, bucket: list[str]) -> None:
            async for raw in stream:
                line = raw.decode("utf-8", errors="replace").rstrip()
                bucket.append(line)
                if on_line and line.strip():
                    on_line(line)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read(proc.stdout, stdout_lines),
                    _read(proc.stderr, stderr_lines),
                ),
                timeout=self._timeout,
            )
            await proc.wait()
        except TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            stderr_lines.append(f"Terraform superó el timeout de {self._timeout}s.")
            tf_result = TerraformResult(
                returncode=124,
                stdout="\n".join(stdout_lines),
                stderr="\n".join(stderr_lines),
            )
            log_event(
                "terraform.streaming.timeout",
                level="ERROR",
                command=cmd,
                workdir=str(workdir),
                duration_ms=round((perf_counter() - start) * 1000, 2),
                stdout=tf_result.stdout,
                stderr=tf_result.stderr,
            )
            return tf_result
        tf_result = TerraformResult(
            returncode=proc.returncode or 0,
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
        )
        log_event(
            "terraform.streaming.end",
            command=cmd,
            workdir=str(workdir),
            returncode=tf_result.returncode,
            duration_ms=round((perf_counter() - start) * 1000, 2),
            stdout=tf_result.stdout,
            stderr=tf_result.stderr,
        )
        return tf_result
