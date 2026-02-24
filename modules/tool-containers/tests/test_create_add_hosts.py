"""Tests for add_hosts parameter in ContainersTool._op_create.

Verifies that --add-host flags are correctly assembled into (or absent from)
the docker run args based on the add_hosts input parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from amplifier_module_tool_containers import ContainersTool


@dataclass
class FakeResult:
    """Minimal stand-in for a subprocess / runtime result."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _make_tool() -> tuple[ContainersTool, AsyncMock]:
    """Create a ContainersTool with a mocked runtime.run."""
    tool = ContainersTool()
    run_mock = AsyncMock(return_value=FakeResult(returncode=0, stdout="abc123456789\n"))
    tool.runtime.run = run_mock
    return tool, run_mock


def _make_minimal_inp(**overrides: object) -> dict:
    """Minimal create input that skips all provisioning side effects."""
    base: dict = {
        "image": "ubuntu:24.04",
        "forward_git": False,
        "forward_gh": False,
        "forward_ssh": False,
        "mount_cwd": False,
        "dotfiles_skip": True,
    }
    base.update(overrides)
    return base


def _extract_docker_run_args(run_mock: AsyncMock) -> list[str]:
    """Find and return the positional args from the docker run call."""
    for call in run_mock.call_args_list:
        args = call[0]
        if args and args[0] == "run":
            return list(args)
    raise AssertionError("No docker run call found in runtime.run calls")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_hosts_flag_appears_in_docker_run_args() -> None:
    """When add_hosts is set, --add-host entries must appear in the docker run args."""
    tool, run_mock = _make_tool()
    inp = _make_minimal_inp(add_hosts=["host.docker.internal:host-gateway"])

    with patch.object(tool.store, "save"):
        await tool._op_create(inp)

    args = _extract_docker_run_args(run_mock)
    assert "--add-host" in args, f"Expected --add-host in args but got: {args}"
    idx = args.index("--add-host")
    assert args[idx + 1] == "host.docker.internal:host-gateway"


@pytest.mark.asyncio
async def test_multiple_add_hosts_all_appear_in_docker_run_args() -> None:
    """When add_hosts contains multiple entries, all --add-host pairs must appear."""
    tool, run_mock = _make_tool()
    inp = _make_minimal_inp(add_hosts=["host.docker.internal:host-gateway", "myhost:192.168.1.1"])

    with patch.object(tool.store, "save"):
        await tool._op_create(inp)

    args = _extract_docker_run_args(run_mock)

    # Collect all --add-host values
    add_host_values = [args[i + 1] for i, a in enumerate(args) if a == "--add-host"]
    assert "host.docker.internal:host-gateway" in add_host_values
    assert "myhost:192.168.1.1" in add_host_values


@pytest.mark.asyncio
async def test_empty_add_hosts_produces_no_add_host_args() -> None:
    """When add_hosts is an empty list, no --add-host flags must appear."""
    tool, run_mock = _make_tool()
    inp = _make_minimal_inp(add_hosts=[])

    with patch.object(tool.store, "save"):
        await tool._op_create(inp)

    args = _extract_docker_run_args(run_mock)
    assert "--add-host" not in args, f"Expected no --add-host in args but got: {args}"


@pytest.mark.asyncio
async def test_omitted_add_hosts_produces_no_add_host_args() -> None:
    """When add_hosts is omitted entirely, no --add-host flags must appear (backward compat)."""
    tool, run_mock = _make_tool()
    inp = _make_minimal_inp()  # No add_hosts key

    with patch.object(tool.store, "save"):
        await tool._op_create(inp)

    args = _extract_docker_run_args(run_mock)
    assert "--add-host" not in args, f"Expected no --add-host in args but got: {args}"
