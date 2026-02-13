"""Tests for ContainerRuntime: detection, command execution, caching."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_containers.runtime import ContainerRuntime


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_podman_preferred():
    """When both podman and docker exist, podman wins."""
    runtime = ContainerRuntime()
    with patch(
        "shutil.which",
        side_effect=lambda c: f"/usr/bin/{c}" if c in ("podman", "docker") else None,
    ):
        result = await runtime.detect()
    assert result == "podman"


@pytest.mark.asyncio
async def test_detect_docker_fallback():
    """When only docker exists, use it."""
    runtime = ContainerRuntime()
    with patch(
        "shutil.which",
        side_effect=lambda c: "/usr/bin/docker" if c == "docker" else None,
    ):
        result = await runtime.detect()
    assert result == "docker"


@pytest.mark.asyncio
async def test_detect_none():
    """When neither exists, returns None."""
    runtime = ContainerRuntime()
    with patch("shutil.which", return_value=None):
        result = await runtime.detect()
    assert result is None


@pytest.mark.asyncio
async def test_runtime_caching():
    """Second detect() call returns cached result without re-checking."""
    runtime = ContainerRuntime()
    with patch(
        "shutil.which",
        side_effect=lambda c: "/usr/bin/docker" if c == "docker" else None,
    ) as mock_which:
        first = await runtime.detect()
        second = await runtime.detect()
    assert first == "docker"
    assert second == "docker"
    # shutil.which should only be called during the first detect()
    # (podman check fails, docker check succeeds = 2 calls, then cached)
    assert mock_which.call_count == 2


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _make_mock_process(returncode: int, stdout: bytes, stderr: bytes):
    """Create a mock async subprocess."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_run_success(mock_runtime):
    """Successful command returns stdout with returncode 0."""
    proc = _make_mock_process(0, b"container-abc123\n", b"")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await mock_runtime.run("ps", "-q")
    assert result.returncode == 0
    assert "container-abc123" in result.stdout
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_run_failure(mock_runtime):
    """Failed command returns stderr and nonzero returncode."""
    proc = _make_mock_process(1, b"", b"Error: no such container\n")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await mock_runtime.run("inspect", "nonexistent")
    assert result.returncode == 1
    assert "no such container" in result.stderr


@pytest.mark.asyncio
async def test_run_timeout(mock_runtime):
    """Command exceeding timeout returns returncode -1 and timeout message."""
    proc = AsyncMock()
    proc.kill = MagicMock()
    # The post-kill communicate() call (line 112) just needs to return cleanly
    proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_wait_for(coro, *, timeout=None):
        # Consume the coroutine to avoid "was never awaited" warning
        coro.close()
        raise asyncio.TimeoutError

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with patch("asyncio.wait_for", side_effect=fake_wait_for):
            result = await mock_runtime.run("exec", "test", "sleep", "999", timeout=1)
    assert result.returncode == -1
    assert "timed out" in result.stderr.lower()


@pytest.mark.asyncio
async def test_run_no_runtime():
    """Returns error when no runtime detected."""
    runtime = ContainerRuntime()
    with patch("shutil.which", return_value=None):
        result = await runtime.run("ps")
    assert result.returncode == 1
    assert "no container runtime" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Daemon / permissions helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daemon_check_running(mock_runtime):
    """is_daemon_running returns True when info succeeds."""
    proc = _make_mock_process(0, b"{}", b"")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        assert await mock_runtime.is_daemon_running() is True


@pytest.mark.asyncio
async def test_daemon_check_not_running(mock_runtime):
    """is_daemon_running returns False when info fails."""
    proc = _make_mock_process(1, b"", b"Cannot connect")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        assert await mock_runtime.is_daemon_running() is False
