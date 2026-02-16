"""Tests for the ComposeManager module."""

from __future__ import annotations

import pytest

from amplifier_module_tool_containers.compose import ComposeManager
from amplifier_module_tool_containers.runtime import CommandResult


@pytest.fixture
def compose_mgr(mock_runtime):
    """ComposeManager with a mocked docker runtime."""
    return ComposeManager(mock_runtime)


# ---------------------------------------------------------------------------
# detect_compose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_compose_available(compose_mgr):
    """detect_compose returns True when compose version succeeds."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=0, stdout="Docker Compose version v2.24.0", stderr="")

    compose_mgr.runtime.run = _mock_run
    assert await compose_mgr.detect_compose() is True


@pytest.mark.asyncio
async def test_detect_compose_unavailable(compose_mgr):
    """detect_compose returns False when compose is not installed."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(
            returncode=1, stdout="", stderr="docker: 'compose' is not a docker command"
        )

    compose_mgr.runtime.run = _mock_run
    assert await compose_mgr.detect_compose() is False


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_up_builds_correct_args(compose_mgr):
    """up() passes correct flags to docker compose."""
    captured_args: list[str] = []

    async def _mock_run(*args, **kwargs):
        captured_args.extend(args)
        return CommandResult(returncode=0, stdout="", stderr="")

    compose_mgr.runtime.run = _mock_run

    result = await compose_mgr.up("/tmp/compose.yml", "my-project")
    assert result.success is True
    assert "compose" in captured_args
    assert "-f" in captured_args
    assert "/tmp/compose.yml" in captured_args
    assert "-p" in captured_args
    assert "my-project" in captured_args
    assert "up" in captured_args
    assert "-d" in captured_args


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_down_builds_correct_args(compose_mgr):
    """down() passes project name and --remove-orphans."""
    captured_args: list[str] = []

    async def _mock_run(*args, **kwargs):
        captured_args.extend(args)
        return CommandResult(returncode=0, stdout="", stderr="")

    compose_mgr.runtime.run = _mock_run

    result = await compose_mgr.down("my-project")
    assert result.success is True
    assert "-p" in captured_args
    assert "my-project" in captured_args
    assert "down" in captured_args
    assert "--remove-orphans" in captured_args


# ---------------------------------------------------------------------------
# ps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ps_parses_json_array(compose_mgr):
    """ps() parses JSON array output from compose ps."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(
            returncode=0,
            stdout='[{"Service":"db","State":"running"},{"Service":"redis","State":"running"}]',
            stderr="",
        )

    compose_mgr.runtime.run = _mock_run

    services = await compose_mgr.ps("my-project")
    assert len(services) == 2
    assert services[0]["Service"] == "db"


@pytest.mark.asyncio
async def test_ps_parses_jsonl(compose_mgr):
    """ps() handles one-JSON-object-per-line format."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(
            returncode=0,
            stdout='{"Service":"db","State":"running"}\n{"Service":"redis","State":"running"}\n',
            stderr="",
        )

    compose_mgr.runtime.run = _mock_run

    services = await compose_mgr.ps("my-project")
    assert len(services) == 2


@pytest.mark.asyncio
async def test_ps_handles_failure(compose_mgr):
    """ps() returns empty list on failure."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=1, stdout="", stderr="error")

    compose_mgr.runtime.run = _mock_run

    services = await compose_mgr.ps("my-project")
    assert services == []


# ---------------------------------------------------------------------------
# get_network_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_network_name_exists(compose_mgr):
    """get_network_name returns project_default when network exists."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=0, stdout="[{}]", stderr="")

    compose_mgr.runtime.run = _mock_run

    name = await compose_mgr.get_network_name("my-project")
    assert name == "my-project_default"


@pytest.mark.asyncio
async def test_get_network_name_missing(compose_mgr):
    """get_network_name returns None when network doesn't exist."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=1, stdout="", stderr="not found")

    compose_mgr.runtime.run = _mock_run

    name = await compose_mgr.get_network_name("my-project")
    assert name is None
