"""Container image build & push (MEJORAS.md §2.1).

Locates the project's Dockerfile, builds an image tagged with the current git
SHA, and pushes it to a registry. Streaming output is routed back through the
caller-supplied emitter so the TUI can render progress live.

This module is registry-agnostic: it speaks plain ``docker``. Registry-specific
bootstrap (creating an ECR repository, fetching a login token, etc.) lives in
``providers/<cloud>/registry.py``.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

LineHandler = Callable[[str], None]

DEFAULT_PLATFORM = "linux/amd64"


@dataclass
class BuildResult:
    ok: bool
    image_uri: str | None = None
    tag: str | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""


def detect_dockerfile(project_path: Path) -> Path | None:
    """Returns the Dockerfile path if one exists at the project root or in ``docker/``."""
    candidates = [
        project_path / "Dockerfile",
        project_path / "docker" / "Dockerfile",
        project_path / "deploy" / "Dockerfile",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def compute_image_tag(project_path: Path) -> str:
    """Image tag based on the current git SHA; falls back to a timestamp.

    The short SHA is preferred because it stays stable while iterating locally
    and ties the image to a reviewable commit. Without git the timestamp keeps
    uniqueness across builds.
    """
    sha = _git_short_sha(project_path)
    if sha:
        return sha
    import time
    return f"build-{int(time.time())}"


def _git_short_sha(project_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            return sha or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def is_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Client.Version}}"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


async def _stream_subprocess(
    cmd: list[str],
    cwd: Path | None,
    on_line: LineHandler | None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Runs ``cmd`` capturing both streams and forwarding each stdout line."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    async def _drain(stream, sink, forward):
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\n")
            sink.append(decoded)
            if forward and on_line:
                on_line(decoded)

    await asyncio.gather(
        _drain(proc.stdout, stdout_chunks, True),
        _drain(proc.stderr, stderr_chunks, False),
    )
    rc = await proc.wait()
    return rc, "\n".join(stdout_chunks), "\n".join(stderr_chunks)


async def build_image(
    project_path: Path,
    image_uri: str,
    tag: str,
    dockerfile: Path | None = None,
    platform: str = DEFAULT_PLATFORM,
    on_line: LineHandler | None = None,
    build_args: dict[str, str] | None = None,
) -> BuildResult:
    """Runs ``docker build -t <uri>:<tag>`` against the project directory."""
    if not is_docker_available():
        return BuildResult(ok=False, error="docker CLI is not available")

    dockerfile = dockerfile or detect_dockerfile(project_path)
    if dockerfile is None:
        return BuildResult(ok=False, error="No Dockerfile found in project root, docker/, or deploy/")

    full_tag = f"{image_uri}:{tag}"
    latest_tag = f"{image_uri}:latest"

    cmd = [
        "docker",
        "build",
        "-f",
        str(dockerfile),
        "-t",
        full_tag,
        "-t",
        latest_tag,
        "--platform",
        platform,
    ]
    for key, value in (build_args or {}).items():
        cmd.extend(["--build-arg", f"{key}={value}"])
    cmd.append(str(project_path))

    rc, stdout, stderr = await _stream_subprocess(cmd, cwd=project_path, on_line=on_line)
    if rc != 0:
        return BuildResult(ok=False, error=stderr or stdout or "docker build failed", stdout=stdout, stderr=stderr)
    return BuildResult(ok=True, image_uri=full_tag, tag=tag, stdout=stdout, stderr=stderr)


async def docker_login(
    registry: str,
    username: str,
    password: str,
    on_line: LineHandler | None = None,
) -> BuildResult:
    """Authenticates the local Docker daemon against ``registry`` via stdin password."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "login", "-u", username, "--password-stdin", registry,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate(input=password.encode("utf-8"))
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if on_line and stdout:
        for line in stdout.splitlines():
            on_line(line)
    if proc.returncode != 0:
        return BuildResult(ok=False, error=stderr or stdout or "docker login failed")
    return BuildResult(ok=True, stdout=stdout, stderr=stderr)


async def push_image(
    image_with_tag: str,
    on_line: LineHandler | None = None,
) -> BuildResult:
    """Pushes a tag (e.g. ``123.dkr.ecr.us-east-1.amazonaws.com/app:abcd123``)."""
    if not is_docker_available():
        return BuildResult(ok=False, error="docker CLI is not available")

    rc, stdout, stderr = await _stream_subprocess(
        ["docker", "push", image_with_tag], cwd=None, on_line=on_line
    )
    if rc != 0:
        return BuildResult(ok=False, error=stderr or stdout or "docker push failed", stdout=stdout, stderr=stderr)
    return BuildResult(ok=True, image_uri=image_with_tag, stdout=stdout, stderr=stderr)
