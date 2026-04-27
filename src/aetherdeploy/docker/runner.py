from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ContainerInfo:
    name: str
    status: str
    ports: list[str] = field(default_factory=list)


@dataclass
class DockerResult:
    returncode: int
    stdout: str
    stderr: str
    containers: list[ContainerInfo] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class DockerRunner:
    """Controla Docker Compose via subprocess.

    Usa `docker compose` (V2, plugin) con fallback a `docker-compose` (V1).
    """

    def __init__(self) -> None:
        self._cmd = self._detect_compose_cmd()

    def up(self, compose_file: Path, detach: bool = True) -> DockerResult:
        args = ["up", "--build", "--force-recreate"]
        if detach:
            args.append("-d")
        return self._run(args, compose_file)

    def down(self, compose_file: Path, volumes: bool = False) -> DockerResult:
        args = ["down"]
        if volumes:
            args.append("-v")
        return self._run(args, compose_file)

    def ps(self, compose_file: Path) -> DockerResult:
        return self._run(["ps", "--format=json"], compose_file)

    def logs(self, compose_file: Path, service: str | None = None, tail: int = 50) -> DockerResult:
        args = ["logs", f"--tail={tail}"]
        if service:
            args.append(service)
        return self._run(args, compose_file)

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                self._cmd + ["version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_service_urls(self, compose_file: Path, port: int) -> list[str]:
        """Retorna las URLs locales de los servicios expuestos en el puerto dado."""
        return [f"http://localhost:{port}"]

    def _run(self, args: list[str], compose_file: Path) -> DockerResult:
        cmd = self._cmd + ["-f", str(compose_file), *args]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(compose_file.parent),
        )
        return DockerResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    @staticmethod
    def _detect_compose_cmd() -> list[str]:
        # Docker Compose V2 (plugin integrado en docker)
        try:
            r = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=3)
            if r.returncode == 0:
                return ["docker", "compose"]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Fallback V1
        return ["docker-compose"]
