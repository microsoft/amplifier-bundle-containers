"""Safety policies for container operations.

Provides approval gates for dangerous container operations, session-scoped
cleanup, and container count limits. This is an OPTIONAL behavior — include
it for environments where safety policies are desired.
"""

from __future__ import annotations

import logging
from typing import Any

__amplifier_module_type__ = "hook"

logger = logging.getLogger(__name__)


# Paths that require approval when used as bind mount sources
DEFAULT_SENSITIVE_PREFIXES = (
    "/",
    "/etc",
    "/var",
    "/root",
    "/home",
    "/boot",
    "/sys",
    "/proc",
)


class ContainerSafetyHooks:
    """Hook handler that enforces safety policies on container operations."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.require_approval = set(
            self.config.get(
                "require_approval_for",
                [
                    "gpu_access",
                    "host_network",
                    "sensitive_mounts",
                    "ssh_forwarding",
                    "all_env_passthrough",
                    "destroy_all",
                ],
            )
        )
        self.sensitive_prefixes = tuple(
            self.config.get("sensitive_mount_prefixes", DEFAULT_SENSITIVE_PREFIXES)
        )
        self.max_containers = self.config.get("max_containers_per_session", 10)
        self.auto_cleanup = self.config.get("auto_cleanup_on_session_end", True)
        self._session_containers: list[str] = []

    async def handle_tool_pre(self, event: dict[str, Any]) -> dict[str, Any]:
        """Inspect container tool calls and enforce policies."""
        tool_name = event.get("tool_name", "")
        tool_input = event.get("tool_input", {})

        if tool_name != "containers":
            return {"action": "continue"}

        operation = tool_input.get("operation", "")
        reasons: list[str] = []

        # Check: GPU access
        if (
            operation == "create"
            and tool_input.get("gpu")
            and "gpu_access" in self.require_approval
        ):
            reasons.append("GPU passthrough requested (--gpus all)")

        # Check: Host network
        if (
            operation == "create"
            and tool_input.get("network") == "host"
            and "host_network" in self.require_approval
        ):
            reasons.append("Host network mode requested (no network isolation)")

        # Check: Sensitive mounts
        if operation == "create" and "sensitive_mounts" in self.require_approval:
            for mount in tool_input.get("mounts", []):
                host_path = mount.get("host", "")
                if self._is_sensitive_path(host_path):
                    reasons.append(f"Sensitive host path mount: {host_path}")

        # Check: SSH forwarding
        if (
            operation == "create"
            and tool_input.get("forward_ssh")
            and "ssh_forwarding" in self.require_approval
        ):
            reasons.append("SSH key forwarding requested (private key access)")

        # Check: All env passthrough
        if (
            operation == "create"
            and tool_input.get("env_passthrough") == "all"
            and "all_env_passthrough" in self.require_approval
        ):
            reasons.append("All environment variables requested (may include secrets)")

        # Check: Destroy all
        if operation == "destroy_all" and "destroy_all" in self.require_approval:
            reasons.append("Destroying ALL managed containers")

        # Check: Container limit
        if operation == "create" and len(self._session_containers) >= self.max_containers:
            return {
                "action": "deny",
                "reason": (
                    f"Container limit reached ({self.max_containers}). "
                    "Destroy existing containers before creating new ones."
                ),
            }

        if reasons:
            return {
                "action": "ask_user",
                "message": (
                    "Container safety review required:\n"
                    + "\n".join(f"  - {r}" for r in reasons)
                    + "\n\nAllow this operation?"
                ),
            }

        return {"action": "continue"}

    async def handle_tool_post(self, event: dict[str, Any]) -> dict[str, Any]:
        """Track containers created in this session."""
        tool_name = event.get("tool_name", "")
        tool_input = event.get("tool_input", {})
        tool_output = event.get("tool_output", {})

        if tool_name != "containers":
            return {"action": "continue"}

        operation = tool_input.get("operation", "")

        # Track created containers
        if operation == "create" and isinstance(tool_output, dict):
            container_name = tool_output.get("container")
            if container_name and tool_output.get("success"):
                self._session_containers.append(container_name)

        # Track destroyed containers
        if operation in ("destroy", "destroy_all") and isinstance(tool_output, dict):
            if operation == "destroy":
                container_name = tool_input.get("container", "")
                if container_name in self._session_containers:
                    self._session_containers.remove(container_name)
            elif operation == "destroy_all":
                self._session_containers.clear()

        return {"action": "continue"}

    async def handle_session_end(self, event: dict[str, Any]) -> dict[str, Any]:
        """Clean up non-persistent containers when session ends."""
        if not self.auto_cleanup or not self._session_containers:
            return {"action": "continue"}

        logger.info(
            "Session ending — cleaning up %d container(s): %s",
            len(self._session_containers),
            ", ".join(self._session_containers),
        )
        # The actual cleanup is best-effort — we emit the intent
        # and the orchestrator/tool handles execution
        return {
            "action": "continue",
            "cleanup_containers": list(self._session_containers),
        }

    def _is_sensitive_path(self, path: str) -> bool:
        """Check if a host path is considered sensitive."""
        if not path:
            return False
        # Exact match on sensitive prefixes (but not subdirs of /home/user/projects)
        # /home is sensitive, /home/user/projects/myapp is not
        normalized = path.rstrip("/")
        for prefix in self.sensitive_prefixes:
            prefix = prefix.rstrip("/")
            if normalized == prefix:
                return True
        return False


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> list[Any]:
    """Amplifier module mount point."""
    hooks = ContainerSafetyHooks(config=config)

    # Register hook handlers
    if hasattr(coordinator, "hooks"):
        coordinator.hooks.register("tool:pre", hooks.handle_tool_pre)
        coordinator.hooks.register("tool:post", hooks.handle_tool_post)
        coordinator.hooks.register("session:end", hooks.handle_session_end)

    return [hooks]
