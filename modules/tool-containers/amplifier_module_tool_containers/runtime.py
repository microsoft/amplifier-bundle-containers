"""Container runtime detection and command execution."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Result of a container runtime command."""

    returncode: int
    stdout: str
    stderr: str


class ContainerRuntime:
    """Detects and wraps Docker or Podman CLI."""

    def __init__(self) -> None:
        self._runtime: str | None = None

    async def detect(self) -> str | None:
        """Return 'docker' or 'podman' or None. Prefer podman (rootless)."""
        if self._runtime is not None:
            return self._runtime
        for candidate in ("podman", "docker"):
            if shutil.which(candidate):
                self._runtime = candidate
                return candidate
        return None

    async def run(self, *args: str, timeout: int = 300) -> CommandResult:
        """Execute a runtime command and return structured result."""
        runtime = await self.detect()
        if runtime is None:
            return CommandResult(
                returncode=1,
                stdout="",
                stderr="No container runtime (docker/podman) found on PATH",
            )
        proc = await asyncio.create_subprocess_exec(
            runtime,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return CommandResult(
                returncode=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
            )
        return CommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
        )

    async def is_daemon_running(self) -> bool:
        result = await self.run("info", "--format", "json", timeout=10)
        return result.returncode == 0

    async def user_has_permissions(self) -> bool:
        result = await self.run("ps", "-q", timeout=10)
        return result.returncode == 0
