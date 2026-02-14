"""Tests for purpose profile resolution (resolve_purpose + PURPOSE_PROFILES)."""

from __future__ import annotations

from amplifier_module_tool_containers.images import get_profile_hash, resolve_purpose


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
