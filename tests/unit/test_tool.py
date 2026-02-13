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
        result = await tool.execute(
            "containers", {"operation": "create", "name": "test"}
        )
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
