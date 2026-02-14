"""Container management tool for Amplifier agents.

Provides operations for creating, managing, and destroying isolated container
environments using Docker or Podman. Supports environment variable passthrough,
git/GH/SSH credential forwarding, dotfiles integration, purpose-based smart
defaults, and container lifecycle management.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .images import resolve_purpose
from .provisioner import ContainerProvisioner, ProvisioningStep, resolve_env_passthrough
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
        self.provisioner = ContainerProvisioner(self.runtime)
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
                                "cache_clear",
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
                        "repo_url": {
                            "type": "string",
                            "description": "Git URL to clone (used with purpose='try-repo')",
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
                        "user": {
                            "type": "string",
                            "description": "Container user (default: host UID:GID for mounted volumes, 'root' for root access)",
                        },
                        "force": {"type": "boolean", "default": False},
                        "confirm": {"type": "boolean", "default": False},
                        "health_check": {"type": "boolean", "default": False},
                        "host_path": {"type": "string"},
                        "container_path": {"type": "string"},
                        "snapshot": {
                            "type": "string",
                            "description": "Snapshot name (for snapshot/restore)",
                        },
                        "cache_bust": {
                            "type": "boolean",
                            "default": False,
                            "description": "Ignore cached image, build fresh",
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

    # -- Caching -------------------------------------------------------------

    async def _get_cached_image(self, purpose: str) -> str | None:
        """Check if a locally cached image exists and is current for this purpose."""
        from .images import get_profile_hash

        cache_tag = f"amplifier-cache:{purpose}"
        result = await self.runtime.run(
            "image",
            "inspect",
            "--format",
            '{{index .Config.Labels "amplifier.cache.version"}}',
            cache_tag,
            timeout=10,
        )
        if result.returncode != 0:
            return None  # No cached image

        # Verify cache version matches current profile definition
        expected_hash = get_profile_hash(purpose)
        if expected_hash:
            cached_hash = result.stdout.strip()
            if cached_hash != expected_hash:
                return None  # Cache is stale

        return cache_tag

    async def _cache_image(self, container: str, purpose: str) -> None:
        """Commit container state as a cached image for this purpose."""
        from .images import get_profile_hash

        version_hash = get_profile_hash(purpose)
        cache_tag = f"amplifier-cache:{purpose}"

        args = ["commit"]
        if version_hash:
            args.extend(["--change", f"LABEL amplifier.cache.version={version_hash}"])
        args.extend([container, cache_tag])

        await self.runtime.run(*args, timeout=120)

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
                    None if daemon_ok else f"Start the daemon: sudo systemctl start {runtime}"
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
                    "detail": "User can access runtime" if perms_ok else "Permission denied",
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
                        None if disk_passed else f"Free disk space or run: {runtime} system prune"
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
        # Handle try-repo auto-detection
        purpose = inp.get("purpose")
        if purpose == "try-repo":
            repo_url = inp.get("repo_url")
            if not repo_url:
                return {"error": "repo_url is required when purpose is 'try-repo'"}

            from .images import detect_repo_purpose

            detected_purpose, setup_hints = await detect_repo_purpose(repo_url)
            inp["purpose"] = detected_purpose
            purpose = detected_purpose

            # Prepend clone + cd + setup hints to setup_commands
            user_setup = inp.get("setup_commands", [])
            inp["setup_commands"] = (
                [
                    f"git clone {repo_url} /workspace/repo",
                ]
                + [f"cd /workspace/repo && {hint}" for hint in setup_hints]
                + user_setup
            )

        # Resolve purpose profile
        if purpose:
            inp = resolve_purpose(purpose, inp)

        # Check for cached image (skip if cache_bust=True or no purpose)
        cache_used = False
        if purpose and not inp.get("cache_bust", False):
            cached_image = await self._get_cached_image(purpose)
            if cached_image:
                inp["image"] = cached_image
                cache_used = True
                # Remove profile setup commands, keep only user's explicit ones
                profile_cmds = inp.get("_profile_setup_commands", [])
                all_cmds = inp.get("setup_commands", [])
                user_cmds = all_cmds[len(profile_cmds) :]
                inp["setup_commands"] = user_cmds

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

        # SSH key mount (must be at creation time) — staged to /tmp/.host-ssh
        # so the provisioner can copy with correct ownership into container $HOME
        if inp.get("forward_ssh", False):
            ssh_dir = Path.home() / ".ssh"
            if ssh_dir.exists():
                args.extend(["-v", f"{ssh_dir}:/tmp/.host-ssh:ro"])

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

        # Default to host UID:GID for proper file ownership on mounted volumes
        user_flag = inp.get("user")
        if user_flag is None and (inp.get("mount_cwd", True) or inp.get("mounts")):
            uid = os.getuid()
            gid = os.getgid()
            user_flag = f"{uid}:{gid}"

        if user_flag and user_flag != "root":
            args.extend(["--user", user_flag])

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

        # Collect provisioning report
        report: list[ProvisioningStep] = []

        # Env passthrough (already done via -e flags, just report it)
        report.append(
            ProvisioningStep("env_passthrough", "success", f"{len(env_vars)} variables injected")
        )

        # Git config
        if inp.get("forward_git", True):
            report.append(await self.provisioner.provision_git(name))
        else:
            report.append(ProvisioningStep("forward_git", "skipped", "Not requested"))

        # GH auth
        if inp.get("forward_gh", True):
            report.append(await self.provisioner.provision_gh_auth(name))
        else:
            report.append(ProvisioningStep("forward_gh", "skipped", "Not requested"))

        # SSH permissions
        if inp.get("forward_ssh", False):
            report.append(await self.provisioner.fix_ssh_permissions(name))
        else:
            report.append(ProvisioningStep("forward_ssh", "skipped", "Not requested"))

        # Dotfiles
        if not inp.get("dotfiles_skip", False):
            dotfiles_repo = inp.get(
                "dotfiles_repo",
                self.config.get("dotfiles", {}).get("repo"),
            )
            if dotfiles_repo:
                report.append(
                    await self.provisioner.provision_dotfiles(
                        name,
                        repo=dotfiles_repo,
                        script=inp.get("dotfiles_script"),
                        branch=inp.get("dotfiles_branch"),
                        target=inp.get("dotfiles_target", "~/.dotfiles"),
                    )
                )
            elif inp.get("dotfiles_inline"):
                report.append(
                    await self.provisioner.provision_dotfiles_inline(name, inp["dotfiles_inline"])
                )
            else:
                report.append(ProvisioningStep("dotfiles", "skipped", "No dotfiles configured"))
        else:
            report.append(ProvisioningStep("dotfiles", "skipped", "Explicitly skipped"))

        # Setup commands (track each individually)
        setup_commands = inp.get("setup_commands", [])
        if setup_commands:
            cmd_results = []
            for cmd in setup_commands:
                cmd_result = await self.runtime.run("exec", name, "/bin/sh", "-c", cmd, timeout=300)
                if cmd_result.returncode != 0:
                    cmd_results.append(
                        {"command": cmd, "status": "failed", "error": cmd_result.stderr.strip()}
                    )
                else:
                    cmd_results.append({"command": cmd, "status": "success"})

            all_ok = all(r["status"] == "success" for r in cmd_results)
            succeeded = sum(1 for r in cmd_results if r["status"] == "success")
            report.append(
                ProvisioningStep(
                    "setup_commands",
                    "success" if all_ok else "partial",
                    f"{succeeded}/{len(cmd_results)} commands succeeded",
                    error=None
                    if all_ok
                    else str([r for r in cmd_results if r["status"] == "failed"]),
                )
            )

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

        # Cache the image for next time (only for purpose-based creates without cache)
        if purpose and not cache_used and not inp.get("cache_bust", False):
            await self._cache_image(name, purpose)

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
            "cache_used": cache_used,
            "provisioning_report": [
                {"name": s.name, "status": s.status, "detail": s.detail, "error": s.error}
                for s in report
            ],
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
            result = await self.runtime.run("exec", container, "test", "-x", shell, timeout=5)
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
        result = await self.runtime.run("inspect", "--format", "json", container, timeout=10)
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
            "detail": result.stderr.strip() if result.returncode != 0 else "Snapshot created",
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
            "detail": result.stderr.strip() if result.returncode != 0 else "Network created",
        }

    async def _op_destroy_network(self, inp: dict[str, Any]) -> dict[str, Any]:
        name = inp.get("name", "")
        if not name:
            return {"error": "'name' is required"}
        result = await self.runtime.run("network", "rm", name, timeout=15)
        return {
            "success": result.returncode == 0,
            "network": name,
            "detail": result.stderr.strip() if result.returncode != 0 else "Network removed",
        }

    async def _op_cache_clear(self, inp: dict[str, Any]) -> dict[str, Any]:
        """Remove locally cached purpose images."""
        purpose = inp.get("purpose")
        if purpose:
            cache_tag = f"amplifier-cache:{purpose}"
            result = await self.runtime.run("rmi", cache_tag, timeout=15)
            return {
                "success": result.returncode == 0,
                "cleared": [purpose] if result.returncode == 0 else [],
                "detail": result.stderr.strip()
                if result.returncode != 0
                else f"Cleared cache for {purpose}",
            }
        # Clear all amplifier-cache:* images
        list_result = await self.runtime.run(
            "images",
            "--format",
            "{{.Repository}}:{{.Tag}}",
            "--filter",
            "reference=amplifier-cache:*",
            timeout=10,
        )
        cleared: list[str] = []
        if list_result.returncode == 0 and list_result.stdout.strip():
            for image_tag in list_result.stdout.strip().split("\n"):
                rm_result = await self.runtime.run("rmi", image_tag.strip(), timeout=15)
                if rm_result.returncode == 0:
                    cleared.append(image_tag.strip())
        return {
            "success": True,
            "cleared": cleared,
            "detail": f"Cleared {len(cleared)} cached images"
            if cleared
            else "No cached images found",
        }


# ---------------------------------------------------------------------------
# Module mount point
# ---------------------------------------------------------------------------


def mount(coordinator: Any, config: dict[str, Any] | None = None) -> list[Any]:
    """Amplifier module mount point."""
    tool = ContainersTool(config=config)
    return [tool]
