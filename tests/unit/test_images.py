"""Tests for purpose profile resolution (resolve_purpose + PURPOSE_PROFILES)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_containers.images import (
    REPO_MARKERS,
    detect_repo_purpose,
    get_profile_hash,
    resolve_purpose,
)


def test_resolve_python():
    """Returns python:3.12-slim image, includes uv setup commands."""
    result = resolve_purpose("python", {})
    assert result["image"] == "python:3.12-slim"
    setup = " ".join(result.get("setup_commands", []))
    assert "uv" in setup


def test_resolve_amplifier():
    """Returns correct image with amplifier install commands."""
    result = resolve_purpose("amplifier", {})
    assert result["image"] == "python:3.12-slim"
    setup = " ".join(result.get("setup_commands", []))
    assert "amplifier" in setup


def test_resolve_clean():
    """Clean purpose sets dotfiles_skip=True, forward_git/gh/ssh all False."""
    result = resolve_purpose("clean", {})
    assert result.get("dotfiles_skip") is True
    assert result.get("forward_git") is False
    assert result.get("forward_gh") is False
    assert result.get("forward_ssh") is False


def test_resolve_general():
    """Returns ubuntu:24.04 with common packages."""
    result = resolve_purpose("general", {})
    assert result["image"] == "ubuntu:24.04"
    setup = " ".join(result.get("setup_commands", []))
    # general profile has packages like git, curl, jq, etc.
    assert "apt-get" in setup
    assert "git" in setup


def test_explicit_overrides_purpose():
    """Explicit image param beats purpose default."""
    result = resolve_purpose("python", {"image": "my-custom:latest"})
    assert result["image"] == "my-custom:latest"


def test_unknown_purpose_passthrough():
    """Unknown purpose returns explicit params unchanged."""
    explicit = {"image": "alpine:3.19", "env": {"FOO": "bar"}}
    result = resolve_purpose("unknown-thing", explicit)
    assert result == explicit


def test_setup_commands_prepended():
    """Purpose setup_commands come before explicit ones."""
    result = resolve_purpose("python", {"setup_commands": ["echo done"]})
    cmds = result["setup_commands"]
    # Purpose commands (apt-get, uv) should come before the explicit one
    assert cmds[-1] == "echo done"
    assert len(cmds) >= 2
    assert "apt-get" in cmds[0]


def test_purpose_env_merged():
    """Purpose env merged with explicit env (explicit wins)."""
    result = resolve_purpose("python", {"env": {"VIRTUAL_ENV": "/custom", "MY_VAR": "1"}})
    env = result["env"]
    # Explicit wins for VIRTUAL_ENV
    assert env["VIRTUAL_ENV"] == "/custom"
    # User's extra var is kept
    assert env["MY_VAR"] == "1"
    # Purpose's PATH env is still present
    assert "PATH" in env


# ---------------------------------------------------------------------------
# Profile hash tests
# ---------------------------------------------------------------------------


def test_get_profile_hash_known_purpose():
    """get_profile_hash returns a string for known purposes."""
    h = get_profile_hash("python")
    assert isinstance(h, str)
    assert len(h) == 8


def test_get_profile_hash_unknown_purpose():
    """get_profile_hash returns None for unknown purposes."""
    assert get_profile_hash("nonexistent-purpose") is None


def test_get_profile_hash_deterministic():
    """Same purpose always produces the same hash."""
    h1 = get_profile_hash("python")
    h2 = get_profile_hash("python")
    assert h1 == h2


# ---------------------------------------------------------------------------
# Profile command tracking tests
# ---------------------------------------------------------------------------


def test_resolve_purpose_tracks_profile_commands():
    """resolve_purpose includes _profile_setup_commands for cache differentiation."""
    result = resolve_purpose("python", {"setup_commands": ["echo user-cmd"]})
    assert "_profile_setup_commands" in result
    profile_cmds = result["_profile_setup_commands"]
    assert isinstance(profile_cmds, list)
    assert len(profile_cmds) > 0
    # Profile commands should include apt-get and uv setup
    assert any("apt-get" in c for c in profile_cmds)
    # User command should NOT be in profile commands
    assert "echo user-cmd" not in profile_cmds
    # But user command should be in the full setup_commands list
    assert result["setup_commands"][-1] == "echo user-cmd"


# ---------------------------------------------------------------------------
# REPO_MARKERS tests
# ---------------------------------------------------------------------------


def test_repo_markers_priority_order():
    """Cargo.toml should be checked before package.json (Rust > Node)."""
    rust_idx = next(i for i, (f, p) in enumerate(REPO_MARKERS) if p == "rust")
    node_idx = next(i for i, (f, p) in enumerate(REPO_MARKERS) if p == "node")
    assert rust_idx < node_idx


def test_repo_markers_contains_all_expected():
    """All expected languages are covered."""
    purposes = {p for _, p in REPO_MARKERS}
    assert purposes == {"rust", "python", "node", "go"}


# ---------------------------------------------------------------------------
# detect_repo_purpose tests
# ---------------------------------------------------------------------------


def _make_mock_proc(returncode: int = 0, stderr: str = "") -> MagicMock:
    """Create a mock process for asyncio.create_subprocess_exec."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr.encode()))
    return proc


@pytest.mark.asyncio
async def test_detect_python_pyproject(tmp_path: Path):
    """pyproject.toml triggers python purpose with uv install hint."""
    # Create marker file in the temp dir that will act as the clone target
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")

    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp", return_value=str(tmp_path)
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(0),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/example/repo.git")

    assert purpose == "python"
    assert len(hints) >= 1
    assert any("uv pip install" in h for h in hints)


@pytest.mark.asyncio
async def test_detect_node_package_json(tmp_path: Path):
    """package.json triggers node purpose with npm install hint."""
    (tmp_path / "package.json").write_text('{"name":"test"}')

    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp", return_value=str(tmp_path)
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(0),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/example/repo.git")

    assert purpose == "node"
    assert any("npm install" in h for h in hints)


@pytest.mark.asyncio
async def test_detect_rust_cargo(tmp_path: Path):
    """Cargo.toml triggers rust purpose with cargo build hint."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "test"\n')

    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp", return_value=str(tmp_path)
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(0),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/example/repo.git")

    assert purpose == "rust"
    assert any("cargo build" in h for h in hints)


@pytest.mark.asyncio
async def test_detect_go_module(tmp_path: Path):
    """go.mod triggers go purpose with go build hint."""
    (tmp_path / "go.mod").write_text("module example.com/test\n")

    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp", return_value=str(tmp_path)
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(0),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/example/repo.git")

    assert purpose == "go"
    assert any("go build" in h for h in hints)


@pytest.mark.asyncio
async def test_detect_fallback_general(tmp_path: Path):
    """No markers triggers general purpose with no hints."""
    # Empty directory — no marker files
    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp", return_value=str(tmp_path)
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(0),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/example/repo.git")

    assert purpose == "general"
    assert hints == []


@pytest.mark.asyncio
async def test_detect_clone_failure_returns_general():
    """Failed git clone returns general purpose with no hints."""
    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp",
            return_value="/tmp/amp-tryrepo-fake",
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(128, "fatal: repository not found"),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/bad/repo.git")

    assert purpose == "general"
    assert hints == []


@pytest.mark.asyncio
async def test_detect_timeout_returns_general():
    """Timeout during clone returns general purpose with no hints."""

    async def _slow_communicate() -> tuple[bytes, bytes]:
        await asyncio.sleep(999)
        return b"", b""

    proc = MagicMock()
    proc.communicate = _slow_communicate

    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp",
            return_value="/tmp/amp-tryrepo-fake",
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=proc,
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/slow/repo.git")

    assert purpose == "general"
    assert hints == []


@pytest.mark.asyncio
async def test_detect_makefile_adds_make_hint(tmp_path: Path):
    """Makefile adds 'make' as an additional hint."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    (tmp_path / "Makefile").write_text("all:\n\techo hello\n")

    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp", return_value=str(tmp_path)
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(0),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/example/repo.git")

    assert purpose == "python"
    assert any("make" in h for h in hints)
    # Should have both the python hint and the make hint
    assert len(hints) == 2


@pytest.mark.asyncio
async def test_detect_python_requirements_txt(tmp_path: Path):
    """requirements.txt triggers python purpose with pip install hint."""
    (tmp_path / "requirements.txt").write_text("requests\nflask\n")

    with (
        patch(
            "amplifier_module_tool_containers.images.tempfile.mkdtemp", return_value=str(tmp_path)
        ),
        patch(
            "amplifier_module_tool_containers.images.asyncio.create_subprocess_exec",
            return_value=_make_mock_proc(0),
        ),
        patch("amplifier_module_tool_containers.images._shutil.rmtree"),
    ):
        purpose, hints = await detect_repo_purpose("https://github.com/example/repo.git")

    assert purpose == "python"
    assert len(hints) >= 1
    assert any("requirements.txt" in h for h in hints)
