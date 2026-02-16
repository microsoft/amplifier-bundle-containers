"""Docker Compose lifecycle management via CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .runtime import ContainerRuntime


@dataclass
class ComposeResult:
    """Result of a Docker Compose CLI operation."""

    success: bool
    stdout: str
    stderr: str


class ComposeManager:
    """Manages Docker Compose lifecycle via CLI.

    Compose services are created as sibling containers on the Docker daemon.
    The primary workspace container joins the compose network to communicate
    with infrastructure services (databases, caches, etc.) by name.
    """

    def __init__(self, runtime: ContainerRuntime) -> None:
        self.runtime = runtime

    async def detect_compose(self) -> bool:
        """Check if docker compose (v2 plugin) is available."""
        result = await self.runtime.run("compose", "version", timeout=10)
        return result.returncode == 0

    async def up(
        self,
        compose_file: str,
        project_name: str,
        detach: bool = True,
    ) -> ComposeResult:
        """Run docker compose up.

        Args:
            compose_file: Path to the compose YAML file on the host.
            project_name: Compose project name (used for network naming).
            detach: Run in detached mode (default True).
        """
        args = ["compose", "-f", compose_file, "-p", project_name, "up"]
        if detach:
            args.append("-d")
        result = await self.runtime.run(*args, timeout=300)
        return ComposeResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def down(self, project_name: str) -> ComposeResult:
        """Run docker compose down for a project.

        No -f flag needed — docker compose stores project metadata
        and can tear down by project name alone.
        """
        result = await self.runtime.run(
            "compose",
            "-p",
            project_name,
            "down",
            "--remove-orphans",
            timeout=120,
        )
        return ComposeResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def ps(self, project_name: str) -> list[dict[str, Any]]:
        """Get status of compose services.

        Returns a list of service dicts from ``docker compose ps --format json``.
        Returns empty list on failure.
        """
        result = await self.runtime.run(
            "compose",
            "-p",
            project_name,
            "ps",
            "--format",
            "json",
            timeout=10,
        )
        if result.returncode != 0:
            return []
        try:
            data = json.loads(result.stdout)
            # docker compose ps --format json may return a single object
            # or a list depending on version
            if isinstance(data, dict):
                return [data]
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            # Some versions output one JSON object per line (not valid JSON array)
            services: list[dict[str, Any]] = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line:
                    try:
                        services.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return services

    async def get_network_name(self, project_name: str) -> str | None:
        """Get the default network created by compose for this project.

        Compose creates a network named ``{project}_default``.
        Returns the network name if it exists, None otherwise.
        """
        network_name = f"{project_name}_default"
        result = await self.runtime.run(
            "network",
            "inspect",
            network_name,
            timeout=10,
        )
        if result.returncode == 0:
            return network_name
        return None
