"""Tests for ContainersTool dispatch and high-level behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from amplifier_module_tool_containers import ContainersTool
from amplifier_module_tool_containers.runtime import CommandResult


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def test_tool_definitions_valid(tool: ContainersTool):
    """tool_definitions returns valid schema with 'containers' name."""
    defs = tool.tool_definitions
    assert isinstance(defs, list)
    assert len(defs) == 1
    defn = defs[0]
    assert defn["name"] == "containers"
    assert "input_schema" in defn
    schema = defn["input_schema"]
    assert schema["type"] == "object"
    assert "operation" in schema["properties"]
    assert "required" in schema
    assert "operation" in schema["required"]


# ---------------------------------------------------------------------------
# Dispatch errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_operation_returns_error(tool: ContainersTool):
    """execute with bad operation returns error dict."""
    result = await tool.execute("containers", {"operation": "teleport"})
    assert "error" in result
    assert "teleport" in result["error"]


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_all_pass(tool: ContainersTool):
    """Mock runtime methods to all return success."""
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "{}", ""))
    tool.runtime.is_daemon_running = AsyncMock(return_value=True)
    tool.runtime.user_has_permissions = AsyncMock(return_value=True)
    result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is True
    assert result["runtime"] == "docker"
    assert all(c["passed"] for c in result["checks"])


@pytest.mark.asyncio
async def test_preflight_no_runtime(tool: ContainersTool):
    """Mock detect() to return None."""
    tool.runtime._runtime = None  # Reset cache
    with patch("shutil.which", return_value=None):
        result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is False
    assert result["runtime"] is None


# ---------------------------------------------------------------------------
# Auto-preflight on first create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_preflight_on_first_create(tool: ContainersTool):
    """First create triggers preflight automatically; fails if runtime not ready."""
    tool._preflight_passed = False
    tool.runtime._runtime = None  # Force no runtime
    with patch("shutil.which", return_value=None):
        result = await tool.execute("containers", {"operation": "create", "name": "test"})
    assert "error" in result
    assert "not ready" in result["error"].lower()


# ---------------------------------------------------------------------------
# Exec validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_requires_container_and_command(tool: ContainersTool):
    """Returns error if missing container or command."""
    result = await tool.execute("containers", {"operation": "exec"})
    assert "error" in result
    assert "required" in result["error"].lower()

    result = await tool.execute("containers", {"operation": "exec", "container": "c1"})
    assert "error" in result

    result = await tool.execute("containers", {"operation": "exec", "command": "ls"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Destroy all validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destroy_all_requires_confirm(tool: ContainersTool):
    """Returns error without confirm=true."""
    result = await tool.execute("containers", {"operation": "destroy_all"})
    assert "error" in result
    assert "confirm" in result["error"].lower()


# ---------------------------------------------------------------------------
# Copy in validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_in_requires_all_params(tool: ContainersTool):
    """Returns error if missing any of container/host_path/container_path."""
    result = await tool.execute(
        "containers",
        {"operation": "copy_in", "container": "c1", "host_path": "/tmp/f"},
    )
    assert "error" in result

    result = await tool.execute(
        "containers",
        {"operation": "copy_in", "host_path": "/tmp/f", "container_path": "/dst"},
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# List empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(tool: ContainersTool):
    """Returns empty list when no containers."""
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "", ""))
    result = await tool.execute("containers", {"operation": "list"})
    assert result["containers"] == []
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# Provisioning report in create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_provisioning_report(tool: ContainersTool):
    """create operation returns provisioning_report in result."""
    tool._preflight_passed = True

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    # Patch out gh CLI and gitconfig so provisioning takes known paths
    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-report",
                "forward_git": True,
                "forward_gh": True,
                "forward_ssh": False,
            },
        )

    assert "provisioning_report" in result
    report = result["provisioning_report"]
    assert isinstance(report, list)
    assert len(report) > 0
    # Each entry has the required keys
    for entry in report:
        assert "name" in entry
        assert "status" in entry
        assert "detail" in entry
        assert "error" in entry
    # Verify specific steps are present
    step_names = [e["name"] for e in report]
    assert "env_passthrough" in step_names
    assert "forward_git" in step_names
    assert "forward_gh" in step_names
    assert "forward_ssh" in step_names
    assert "dotfiles" in step_names


@pytest.mark.asyncio
async def test_provisioning_report_setup_command_partial(tool: ContainersTool):
    """When some setup_commands fail, report shows partial status."""
    tool._preflight_passed = True
    call_count = 0

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal call_count
        call_count += 1
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # Fail the second setup command (detect by the command content)
        if args and len(args) >= 5 and args[0] == "exec":
            cmd_str = args[4] if len(args) > 4 else ""
            if cmd_str == "failing-command":
                return CommandResult(1, "", "command not found")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-partial",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
                "setup_commands": ["echo hello", "failing-command"],
            },
        )

    assert "provisioning_report" in result
    report = result["provisioning_report"]
    setup_step = next(e for e in report if e["name"] == "setup_commands")
    assert setup_step["status"] == "partial"
    assert "1/2" in setup_step["detail"]
    assert setup_step["error"] is not None


# ---------------------------------------------------------------------------
# Cache clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_clear_requires_no_params(tool: ContainersTool):
    """cache_clear works without purpose (clears all)."""
    # Mock: no cached images found
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "", ""))
    result = await tool.execute("containers", {"operation": "cache_clear"})
    assert result["success"] is True
    assert isinstance(result["cleared"], list)
    assert "detail" in result


@pytest.mark.asyncio
async def test_cache_clear_specific_purpose(tool: ContainersTool):
    """cache_clear with purpose targets a specific image."""
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "", ""))
    result = await tool.execute("containers", {"operation": "cache_clear", "purpose": "python"})
    assert result["success"] is True
    assert result["cleared"] == ["python"]


# ---------------------------------------------------------------------------
# Cache used in create result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_result_includes_cache_used(tool: ContainersTool):
    """create result includes cache_used field."""
    tool._preflight_passed = True

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # Return failure for image inspect (no cache)
        if args and args[0] == "image":
            return CommandResult(1, "", "No such image")
        # commit call succeeds
        if args and args[0] == "commit":
            return CommandResult(0, "sha256:abc\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-cache-field",
                "purpose": "python",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
            },
        )

    assert "cache_used" in result
    assert result["cache_used"] is False  # No cache existed


@pytest.mark.asyncio
async def test_create_uses_cached_image(tool: ContainersTool):
    """create with a cached image sets cache_used=True and skips profile setup."""
    tool._preflight_passed = True
    executed_commands: list[str] = []

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        # Track exec commands to verify profile setup is skipped
        if args and args[0] == "exec" and len(args) > 4:
            executed_commands.append(args[4])
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # Cache hit: image inspect returns matching hash
        if args and args[0] == "image":
            from amplifier_module_tool_containers.images import get_profile_hash

            expected = get_profile_hash("python")
            return CommandResult(0, f"{expected}\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-cache-hit",
                "purpose": "python",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
                "setup_commands": ["echo user-cmd"],
            },
        )

    assert result["cache_used"] is True
    assert result["image"] == "amplifier-cache:python"
    # Profile setup commands (apt-get, uv) should NOT have been executed
    assert not any("apt-get" in c for c in executed_commands)
    # User's explicit command should still have been executed
    assert "echo user-cmd" in executed_commands
