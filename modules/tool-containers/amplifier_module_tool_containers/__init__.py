"""Container management tool for Amplifier agents.

Provides operations for creating, managing, and destroying isolated container
environments using Docker or Podman. Supports environment variable passthrough,
git/GH/SSH credential forwarding, dotfiles integration, purpose-based smart
defaults, and container lifecycle management.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .images import resolve_purpose
from .provisioner import resolve_env_passthrough
from .runtime import ContainerRuntime

__amplifier_module_type__ = "tool"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CreateParams:
    """Parameters for container creation."""

    name: str | None = None
    image: str = "ubuntu:24.04"
    purpose: str | None = None
    workdir: str = "/workspace"
    mounts: list[dict[str, str]] = field(default_factory=list)
    mount_cwd: bool = True
    ports: list[dict[str, int]] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    env_passthrough: str | list[str] = "auto"
    forward_git: bool = True
    forward_gh: bool = True
    forward_ssh: bool = False
    dotfiles_repo: str | None = None
    dotfiles_script: str | None = None
    dotfiles_branch: str | None = None
    dotfiles_target: str = "~/.dotfiles"
    dotfiles_inline: dict[str, str] | None = None
    dotfiles_skip: bool = False
    setup_commands: list[str] = field(default_factory=list)
    memory_limit: str = "4g"
    cpu_limit: float | None = None
    gpu: bool = False
    network: str = "bridge"
    persistent: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Metadata Store
# ---------------------------------------------------------------------------


class MetadataStore:
    """Persistent storage for managed container metadata."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path.home() / ".amplifier" / "containers"
        self.containers_dir = self.base_dir / "containers"

    def save(self, name: str, metadata: dict[str, Any]) -> None:
        path = self.containers_dir / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "metadata.json").write_text(json.dumps(metadata, indent=2))

    def load(self, name: str) -> dict[str, Any] | None:
        path = self.containers_dir / name / "metadata.json"
        if path.exists():
            return json.loads(path.read_text())
        return None

    def remove(self, name: str) -> None:
        path = self.containers_dir / name
        if path.exists():
            shutil.rmtree(path)

    def list_all(self) -> list[dict[str, Any]]:
        if not self.containers_dir.exists():
            return []
        results = []
        for child in sorted(self.containers_dir.iterdir()):
            meta_path = child / "metadata.json"
            if meta_path.exists():
                results.append(json.loads(meta_path.read_text()))
        return results


# ---------------------------------------------------------------------------
# Containers Tool
# ---------------------------------------------------------------------------


class ContainersTool:
    """Manages Docker/Podman containers for isolated workloads."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.runtime = ContainerRuntime()
        self.store = MetadataStore()
        self._preflight_passed = False

    # -- Tool protocol -------------------------------------------------------

    @property
    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "containers",
                "description": (
                    "Manage isolated container environments (Docker/Podman). "
                    "Use for safe repo exploration, clean dev environments, "
                    "parallel workloads, service stacks, and any scenario "
                    "requiring isolation from the host."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "preflight",
                                "create",
                                "exec",
                                "exec_interactive_hint",
                                "list",
                                "status",
                                "destroy",
                                "destroy_all",
                                "copy_in",
                                "copy_out",
                                "snapshot",
                                "restore",
                                "create_network",
                                "destroy_network",
                            ],
                            "description": "Container operation to perform",
                        },
                        "container": {
                            "type": "string",
                            "description": "Container name (for exec/status/destroy/copy/snapshot)",
                        },
                        "name": {
                            "type": "string",
                            "description": "Name for new container or network",
                        },
                        "image": {"type": "string"},
                        "purpose": {
                            "type": "string",
                            "description": (
                                "Smart defaults: python, node, rust, go, "
                                "general, amplifier, try-repo, clean"
                            ),
                        },
                        "command": {
                            "type": "string",
                            "description": "Command to execute (for exec)",
                        },
                        "timeout": {
                            "type": "integer",
                            "default": 300,
                        },
                        "mounts": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Bind mounts: [{host, container, mode}]",
                        },
                        "mount_cwd": {"type": "boolean", "default": True},
                        "ports": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Port mappings: [{host, container}]",
                        },
                        "env": {"type": "object"},
                        "env_passthrough": {
                            "description": '"auto", "all", "none", or list of var names',
                        },
                        "forward_git": {"type": "boolean"},
                        "forward_gh": {"type": "boolean"},
                        "forward_ssh": {"type": "boolean"},
                        "dotfiles_repo": {"type": "string"},
                        "dotfiles_script": {"type": "string"},
                        "dotfiles_branch": {"type": "string"},
                        "dotfiles_target": {"type": "string"},
                        "dotfiles_inline": {"type": "object"},
                        "dotfiles_skip": {"type": "boolean"},
                        "setup_commands": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "memory_limit": {"type": "string", "default": "4g"},
                        "cpu_limit": {"type": "number"},
                        "gpu": {"type": "boolean", "default": False},
                        "network": {"type": "string", "default": "bridge"},
                        "persistent": {"type": "boolean", "default": False},
                        "force": {"type": "boolean", "default": False},
                        "confirm": {"type": "boolean", "default": False},
                        "health_check": {"type": "boolean", "default": False},
                        "host_path": {"type": "string"},
                        "container_path": {"type": "string"},
                        "snapshot": {
                            "type": "string",
                            "description": "Snapshot name (for snapshot/restore)",
                        },
                    },
                    "required": ["operation"],
                },
            }
        ]

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        op = tool_input.get("operation", "")
        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            return {"error": f"Unknown operation: {op}"}

        # Auto-preflight before first create
        if op == "create" and not self._preflight_passed:
            preflight = await self._op_preflight(tool_input)
            if not preflight["ready"]:
                return {
                    "error": "Container runtime not ready. See preflight results.",
                    "preflight": preflight,
                }
            self._preflight_passed = True

        return await handler(tool_input)

    # -- Operations ----------------------------------------------------------

    async def _op_preflight(self, _input: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        # 1. Runtime installed
        runtime = await self.runtime.detect()
        checks.append(
            {
                "name": "runtime_installed",
                "passed": runtime is not None,
                "detail": f"Found: {runtime}" if runtime else "Not found",
                "guidance": (
                    None
                    if runtime
                    else "Install Docker (https://docs.docker.com/get-docker/) "
                    "or Podman (https://podman.io/getting-started/installation)"
                ),
            }
        )

        if runtime is None:
            return {
                "ready": False,
                "runtime": None,
                "checks": checks,
                "summary": "No container runtime found",
            }

        # 2. Daemon running
        daemon_ok = await self.runtime.is_daemon_running()
        checks.append(
            {
                "name": "daemon_running",
                "passed": daemon_ok,
                "detail": "Daemon responding" if daemon_ok else "Daemon not responding",
                "guidance": (
                    None
                    if daemon_ok
                    else f"Start the daemon: sudo systemctl start {runtime}"
                ),
            }
        )

        # 3. User permissions
        if daemon_ok:
            perms_ok = await self.runtime.user_has_permissions()
            checks.append(
                {
                    "name": "user_permissions",
                    "passed": perms_ok,
                    "detail": "User can access runtime"
                    if perms_ok
                    else "Permission denied",
                    "guidance": (
                        None
                        if perms_ok
                        else f"Add user to {runtime} group: sudo usermod -aG {runtime} $USER && newgrp {runtime}"
                    ),
                }
            )
        else:
            checks.append(
                {
                    "name": "user_permissions",
                    "passed": False,
                    "detail": "Skipped (daemon not running)",
                    "guidance": "Start daemon first",
                }
            )

        # 4. Disk space
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free / (1024**3)
            if free_gb < 1:
                disk_passed, disk_detail = False, f"{free_gb:.1f}GB free (need >1GB)"
            elif free_gb < 5:
                disk_passed, disk_detail = (
                    True,
                    f"{free_gb:.1f}GB free (low, consider pruning)",
                )
            else:
                disk_passed, disk_detail = True, f"{free_gb:.1f}GB free"
            checks.append(
                {
                    "name": "disk_space",
                    "passed": disk_passed,
                    "detail": disk_detail,
                    "guidance": (
                        None
                        if disk_passed
                        else f"Free disk space or run: {runtime} system prune"
                    ),
                }
            )
        except OSError:
            checks.append(
                {
                    "name": "disk_space",
                    "passed": True,
                    "detail": "Could not check (non-fatal)",
                    "guidance": None,
                }
            )

        all_passed = all(c["passed"] for c in checks)
        if all_passed:
            self._preflight_passed = True
        return {
            "ready": all_passed,
            "runtime": runtime,
            "checks": checks,
            "summary": "Container runtime ready"
            if all_passed
            else "Prerequisites not met — see checks",
        }

    async def _op_create(self, inp: dict[str, Any]) -> dict[str, Any]:
        # Resolve purpose profile
        purpose = inp.get("purpose")
        if purpose:
            inp = resolve_purpose(purpose, inp)

        # Build create params
        import uuid

        name = inp.get("name") or f"amp-{purpose or 'env'}-{uuid.uuid4().hex[:6]}"
        image = inp.get("image", self.config.get("default_image", "ubuntu:24.04"))
        workdir = inp.get("workdir", "/workspace")

        # Build docker run args
        args: list[str] = [
            "run",
            "-d",
            "--name",
            name,
            "--hostname",
            name,
            "-w",
            workdir,
            # Security hardening
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--memory={inp.get('memory_limit', '4g')}",
            f"--pids-limit={self.config.get('security', {}).get('pids_limit', 256)}",
        ]

        # CPU limit
        cpu_limit = inp.get("cpu_limit")
        if cpu_limit:
            args.extend(["--cpus", str(cpu_limit)])

        # GPU
        if inp.get("gpu"):
            args.extend(["--gpus", "all"])

        # Network
        network = inp.get("network", "bridge")
        args.extend(["--network", network])

        # Mounts
        if inp.get("mount_cwd", True):
            cwd = os.getcwd()
            args.extend(["-v", f"{cwd}:{workdir}"])
        for mount in inp.get("mounts", []):
            mode = mount.get("mode", "rw")
            args.extend(["-v", f"{mount['host']}:{mount['container']}:{mode}"])

        # SSH key mount (must be at creation time)
        if inp.get("forward_ssh", False):
            ssh_dir = Path.home() / ".ssh"
            if ssh_dir.exists():
                args.extend(["-v", f"{ssh_dir}:/root/.ssh:ro"])

        # Ports
        for port in inp.get("ports", []):
            args.extend(["-p", f"{port['host']}:{port['container']}"])

        # Environment variables
        config_patterns = self.config.get("auto_passthrough", {}).get("env_patterns")
        env_vars = resolve_env_passthrough(
            inp.get("env_passthrough", "auto"),
            inp.get("env", {}),
            config_patterns,
        )
        for key, value in env_vars.items():
            args.extend(["-e", f"{key}={value}"])

        # Labels
        now = datetime.now(timezone.utc).isoformat()
        labels = {
            "amplifier.managed": "true",
            "amplifier.bundle": "containers",
            "amplifier.created": now,
            "amplifier.persistent": str(inp.get("persistent", False)).lower(),
        }
        if purpose:
            labels["amplifier.purpose"] = purpose
        labels.update(inp.get("labels", {}))
        for key, value in labels.items():
            args.extend(["-l", f"{key}={value}"])

        # Image + command
        args.append(image)
        args.extend(["sleep", "infinity"])

        # Create the container
        result = await self.runtime.run(*args, timeout=120)
        if result.returncode != 0:
            return {
                "error": f"Failed to create container: {result.stderr.strip()}",
                "command_hint": f"{await self.runtime.detect()} {' '.join(args)}",
            }

        container_id = result.stdout.strip()[:12]

        # Provision: git config
        if inp.get("forward_git", True):
            await self._provision_git(name)

        # Provision: GH auth
        if inp.get("forward_gh", True):
            await self._provision_gh_auth(name)

        # Provision: SSH permissions fix
        if inp.get("forward_ssh", False):
            await self._fix_ssh_permissions(name)

        # Provision: dotfiles
        if not inp.get("dotfiles_skip", False):
            dotfiles_repo = inp.get(
                "dotfiles_repo",
                self.config.get("dotfiles", {}).get("repo"),
            )
            if dotfiles_repo:
                await self._provision_dotfiles(
                    name,
                    repo=dotfiles_repo,
                    script=inp.get("dotfiles_script"),
                    branch=inp.get("dotfiles_branch"),
                    target=inp.get("dotfiles_target", "~/.dotfiles"),
                )
            elif inp.get("dotfiles_inline"):
                await self._provision_dotfiles_inline(name, inp["dotfiles_inline"])

        # Run setup commands
        for cmd in inp.get("setup_commands", []):
            cmd_result = await self.runtime.run(
                "exec", name, "/bin/sh", "-c", cmd, timeout=300
            )
            if cmd_result.returncode != 0:
                # Log but don't fail — setup commands are best-effort
                pass

        # Save metadata
        self.store.save(
            name,
            {
                "name": name,
                "container_id": container_id,
                "image": image,
                "purpose": purpose,
                "created": now,
                "persistent": inp.get("persistent", False),
                "mounts": inp.get("mounts", []),
                "mount_cwd": inp.get("mount_cwd", True),
                "ports": inp.get("ports", []),
                "env_keys": list(env_vars.keys()),
                "provisioning": {
                    "forward_git": inp.get("forward_git", True),
                    "forward_gh": inp.get("forward_gh", True),
                    "forward_ssh": inp.get("forward_ssh", False),
                    "dotfiles_repo": inp.get("dotfiles_repo"),
                },
            },
        )

        # Get interactive hint
        runtime_name = await self.runtime.detect()
        hint = f"{runtime_name} exec -it {name} /bin/bash"

        return {
            "success": True,
            "container": name,
            "container_id": container_id,
            "image": image,
            "purpose": purpose,
            "connect_command": hint,
            "workdir": workdir,
            "env_vars_injected": len(env_vars),
            "persistent": inp.get("persistent", False),
        }

    async def _op_exec(self, inp: dict[str, Any]) -> dict[str, Any]:
        container = inp.get("container", "")
        command = inp.get("command", "")
        timeout = inp.get("timeout", 300)
        if not container or not command:
            return {"error": "Both 'container' and 'command' are required"}
        result = await self.runtime.run(
            "exec", container, "/bin/sh", "-c", command, timeout=timeout
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.returncode == -1,
        }

    async def _op_exec_interactive_hint(self, inp: dict[str, Any]) -> dict[str, Any]:
        container = inp.get("container", "")
        if not container:
            return {"error": "'container' is required"}
        runtime = await self.runtime.detect()
        # Detect best available shell
        for shell in ("/bin/bash", "/bin/zsh", "/bin/sh"):
            result = await self.runtime.run(
                "exec", container, "test", "-x", shell, timeout=5
            )
            if result.returncode == 0:
                return {
                    "command": f"{runtime} exec -it {container} {shell}",
                    "shell": shell,
                    "container": container,
                }
        return {
            "command": f"{runtime} exec -it {container} /bin/sh",
            "shell": "/bin/sh",
            "container": container,
        }

    async def _op_list(self, _inp: dict[str, Any]) -> dict[str, Any]:
        result = await self.runtime.run(
            "ps",
            "-a",
            "--filter",
            "label=amplifier.managed=true",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}",
            timeout=10,
        )
        containers = []
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 3:
                    meta = self.store.load(parts[0])
                    containers.append(
                        {
                            "name": parts[0],
                            "status": parts[1] if len(parts) > 1 else "unknown",
                            "image": parts[2] if len(parts) > 2 else "unknown",
                            "ports": parts[3] if len(parts) > 3 else "",
                            "purpose": (meta or {}).get("purpose"),
                            "persistent": (meta or {}).get("persistent", False),
                        }
                    )
        return {"containers": containers, "count": len(containers)}

    async def _op_status(self, inp: dict[str, Any]) -> dict[str, Any]:
        container = inp.get("container", "")
        if not container:
            return {"error": "'container' is required"}
        result = await self.runtime.run(
            "inspect", "--format", "json", container, timeout=10
        )
        if result.returncode != 0:
            return {"error": f"Container not found: {container}"}
        try:
            info = json.loads(result.stdout)
            if isinstance(info, list):
                info = info[0]
            state = info.get("State", {})
            return {
                "container": container,
                "running": state.get("Running", False),
                "status": state.get("Status", "unknown"),
                "started_at": state.get("StartedAt"),
                "image": info.get("Config", {}).get("Image"),
                "metadata": self.store.load(container),
            }
        except (json.JSONDecodeError, IndexError, KeyError) as exc:
            return {"error": f"Failed to parse status: {exc}"}

    async def _op_destroy(self, inp: dict[str, Any]) -> dict[str, Any]:
        container = inp.get("container", "")
        if not container:
            return {"error": "'container' is required"}
        force = inp.get("force", False)
        # Stop
        stop_cmd = "kill" if force else "stop"
        await self.runtime.run(stop_cmd, container, timeout=30)
        # Remove
        result = await self.runtime.run("rm", "-f", container, timeout=15)
        self.store.remove(container)
        return {
            "success": result.returncode == 0,
            "container": container,
            "detail": "Destroyed" if result.returncode == 0 else result.stderr.strip(),
        }

    async def _op_destroy_all(self, inp: dict[str, Any]) -> dict[str, Any]:
        if not inp.get("confirm", False):
            return {"error": "Set confirm=true to destroy all managed containers"}
        listing = await self._op_list({})
        results = []
        for c in listing.get("containers", []):
            r = await self._op_destroy({"container": c["name"], "force": True})
            results.append(r)
        return {"destroyed": len(results), "results": results}

    async def _op_copy_in(self, inp: dict[str, Any]) -> dict[str, Any]:
        container = inp.get("container", "")
        host_path = inp.get("host_path", "")
        container_path = inp.get("container_path", "")
        if not all([container, host_path, container_path]):
            return {"error": "container, host_path, and container_path are required"}
        result = await self.runtime.run(
            "cp", host_path, f"{container}:{container_path}", timeout=60
        )
        return {
            "success": result.returncode == 0,
            "detail": result.stderr.strip() if result.returncode != 0 else "Copied",
        }

    async def _op_copy_out(self, inp: dict[str, Any]) -> dict[str, Any]:
        container = inp.get("container", "")
        container_path = inp.get("container_path", "")
        host_path = inp.get("host_path", "")
        if not all([container, container_path, host_path]):
            return {"error": "container, container_path, and host_path are required"}
        result = await self.runtime.run(
            "cp", f"{container}:{container_path}", host_path, timeout=60
        )
        return {
            "success": result.returncode == 0,
            "detail": result.stderr.strip() if result.returncode != 0 else "Copied",
        }

    async def _op_snapshot(self, inp: dict[str, Any]) -> dict[str, Any]:
        container = inp.get("container", "")
        snapshot_name = inp.get("name", inp.get("snapshot", ""))
        if not container or not snapshot_name:
            return {"error": "Both 'container' and 'name' are required"}
        image_tag = f"amplifier-snapshot:{snapshot_name}"
        result = await self.runtime.run("commit", container, image_tag, timeout=60)
        return {
            "success": result.returncode == 0,
            "snapshot": snapshot_name,
            "image": image_tag,
            "detail": result.stderr.strip()
            if result.returncode != 0
            else "Snapshot created",
        }

    async def _op_restore(self, inp: dict[str, Any]) -> dict[str, Any]:
        snapshot_name = inp.get("snapshot", "")
        if not snapshot_name:
            return {"error": "'snapshot' is required"}
        # Override image with the snapshot and delegate to create
        inp["image"] = f"amplifier-snapshot:{snapshot_name}"
        inp["operation"] = "create"
        return await self._op_create(inp)

    async def _op_create_network(self, inp: dict[str, Any]) -> dict[str, Any]:
        name = inp.get("name", "")
        if not name:
            return {"error": "'name' is required"}
        result = await self.runtime.run(
            "network",
            "create",
            "--label",
            "amplifier.managed=true",
            name,
            timeout=15,
        )
        return {
            "success": result.returncode == 0,
            "network": name,
            "detail": result.stderr.strip()
            if result.returncode != 0
            else "Network created",
        }

    async def _op_destroy_network(self, inp: dict[str, Any]) -> dict[str, Any]:
        name = inp.get("name", "")
        if not name:
            return {"error": "'name' is required"}
        result = await self.runtime.run("network", "rm", name, timeout=15)
        return {
            "success": result.returncode == 0,
            "network": name,
            "detail": result.stderr.strip()
            if result.returncode != 0
            else "Network removed",
        }

    # -- Provisioning helpers ------------------------------------------------

    async def _provision_git(self, container: str) -> None:
        """Copy git configuration into the container."""
        home = Path.home()
        for src_name, dst_path in [
            (".gitconfig", "/root/.gitconfig"),
            (".gitconfig.local", "/root/.gitconfig.local"),
            (".ssh/known_hosts", "/root/.ssh/known_hosts"),
        ]:
            src = home / src_name
            if src.exists():
                # Ensure target directory exists
                dst_dir = str(Path(dst_path).parent)
                await self.runtime.run(
                    "exec", container, "mkdir", "-p", dst_dir, timeout=5
                )
                await self.runtime.run(
                    "cp", str(src), f"{container}:{dst_path}", timeout=10
                )

    async def _provision_gh_auth(self, container: str) -> None:
        """Forward GitHub CLI authentication into the container."""
        gh_path = shutil.which("gh")
        if not gh_path:
            return
        # Extract token from host gh cli
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "auth",
            "token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return
        token = stdout.decode().strip()
        if not token:
            return
        # Inject as env vars
        env_script = (
            f'echo "export GH_TOKEN={token}" >> /root/.bashrc && '
            f'echo "export GITHUB_TOKEN={token}" >> /root/.bashrc'
        )
        await self.runtime.run(
            "exec", container, "/bin/sh", "-c", env_script, timeout=5
        )
        # If gh is in the container, do full auth login
        gh_check = await self.runtime.run("exec", container, "which", "gh", timeout=5)
        if gh_check.returncode == 0:
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f'echo "{token}" | gh auth login --with-token',
                timeout=15,
            )

    async def _fix_ssh_permissions(self, container: str) -> None:
        """Fix SSH key permissions after bind mount."""
        cmds = [
            "chmod 700 /root/.ssh 2>/dev/null || true",
            "chmod 600 /root/.ssh/id_* 2>/dev/null || true",
            "chmod 644 /root/.ssh/*.pub 2>/dev/null || true",
            "chmod 644 /root/.ssh/known_hosts 2>/dev/null || true",
            "chmod 644 /root/.ssh/config 2>/dev/null || true",
        ]
        for cmd in cmds:
            await self.runtime.run("exec", container, "/bin/sh", "-c", cmd, timeout=5)

    async def _provision_dotfiles(
        self,
        container: str,
        repo: str,
        script: str | None = None,
        branch: str | None = None,
        target: str = "~/.dotfiles",
    ) -> None:
        """Clone and apply dotfiles from a git repo."""
        # Clone
        clone_cmd = "git clone --depth=1"
        if branch:
            clone_cmd += f" --branch {branch}"
        clone_cmd += f" {repo} {target}"
        result = await self.runtime.run(
            "exec", container, "/bin/sh", "-c", clone_cmd, timeout=60
        )
        if result.returncode != 0:
            return  # Clone failed, skip silently

        # Find and run install script
        script_candidates = (
            [script]
            if script
            else ["install.sh", "setup.sh", "bootstrap.sh", "script/setup"]
        )
        for candidate in script_candidates:
            check = await self.runtime.run(
                "exec",
                container,
                "test",
                "-f",
                f"{target}/{candidate}",
                timeout=5,
            )
            if check.returncode == 0:
                await self.runtime.run(
                    "exec",
                    container,
                    "/bin/sh",
                    "-c",
                    f"cd {target} && chmod +x {candidate} && ./{candidate}",
                    timeout=300,
                )
                return

        # Check for Makefile
        make_check = await self.runtime.run(
            "exec",
            container,
            "test",
            "-f",
            f"{target}/Makefile",
            timeout=5,
        )
        if make_check.returncode == 0:
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"cd {target} && make",
                timeout=300,
            )
            return

        # Fallback: smart symlink common dotfiles
        common = [
            ".bashrc",
            ".bash_profile",
            ".bash_aliases",
            ".zshrc",
            ".zprofile",
            ".gitconfig",
            ".gitignore_global",
            ".vimrc",
            ".tmux.conf",
            ".inputrc",
            ".editorconfig",
        ]
        for dotfile in common:
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"test -f {target}/{dotfile} && ln -sf {target}/{dotfile} ~/{dotfile}",
                timeout=5,
            )

    async def _provision_dotfiles_inline(
        self, container: str, files: dict[str, str]
    ) -> None:
        """Write inline dotfiles content into the container."""
        for path, content in files.items():
            escaped = content.replace("'", "'\\''")
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"mkdir -p $(dirname ~/{path}) && cat > ~/{path} << 'AMPLIFIER_DOTFILES_EOF'\n{escaped}\nAMPLIFIER_DOTFILES_EOF",
                timeout=10,
            )


# ---------------------------------------------------------------------------
# Module mount point
# ---------------------------------------------------------------------------


def mount(coordinator: Any, config: dict[str, Any] | None = None) -> list[Any]:
    """Amplifier module mount point."""
    tool = ContainersTool(config=config)
    return [tool]
