"""Tests for environment variable matching and passthrough logic."""

from __future__ import annotations

import os
from unittest.mock import patch

from amplifier_module_tool_containers.provisioner import (
    match_env_patterns,
    resolve_env_passthrough,
)


# ---------------------------------------------------------------------------
# match_env_patterns
# ---------------------------------------------------------------------------


def test_match_api_key_pattern():
    """*_API_KEY matches OPENAI_API_KEY."""
    env = {"OPENAI_API_KEY": "sk-123", "UNRELATED": "nope"}
    matched = match_env_patterns(env, ["*_API_KEY"])
    assert "OPENAI_API_KEY" in matched
    assert "UNRELATED" not in matched


def test_match_prefix_pattern():
    """ANTHROPIC_* matches ANTHROPIC_API_KEY."""
    env = {"ANTHROPIC_API_KEY": "ant-123", "OPENAI_KEY": "sk-456"}
    matched = match_env_patterns(env, ["ANTHROPIC_*"])
    assert "ANTHROPIC_API_KEY" in matched
    assert "OPENAI_KEY" not in matched


def test_no_match():
    """Non-matching vars excluded."""
    env = {"RANDOM_VAR": "value", "ANOTHER": "val2"}
    matched = match_env_patterns(env, ["*_API_KEY"])
    assert len(matched) == 0


def test_never_passthrough_excluded():
    """PATH, HOME, SHELL never passed even with broad patterns."""
    env = {
        "PATH": "/usr/bin",
        "HOME": "/root",
        "SHELL": "/bin/bash",
        "MY_API_KEY": "key1",
    }
    matched = match_env_patterns(env, ["*"])
    assert "PATH" not in matched
    assert "HOME" not in matched
    assert "SHELL" not in matched
    assert "MY_API_KEY" in matched


def test_fnmatch_wildcards():
    """Various patterns work: *_TOKEN, AZURE_*, etc."""
    env = {
        "GH_TOKEN": "ghp_abc",
        "AZURE_OPENAI_KEY": "az-123",
        "AZURE_TENANT_ID": "tenant",
        "PLAIN_VAR": "plain",
    }
    matched = match_env_patterns(env, ["*_TOKEN", "AZURE_*"])
    assert "GH_TOKEN" in matched
    assert "AZURE_OPENAI_KEY" in matched
    assert "AZURE_TENANT_ID" in matched
    assert "PLAIN_VAR" not in matched


# ---------------------------------------------------------------------------
# resolve_env_passthrough
# ---------------------------------------------------------------------------


def _fake_env():
    """A controlled host environment for testing."""
    return {
        "OPENAI_API_KEY": "sk-test",
        "ANTHROPIC_API_KEY": "ant-test",
        "GH_TOKEN": "ghp-test",
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "SHELL": "/bin/bash",
        "RANDOM_VAR": "random",
    }


def test_auto_mode():
    """Auto mode uses DEFAULT_ENV_PATTERNS."""
    with patch.dict(os.environ, _fake_env(), clear=True):
        result = resolve_env_passthrough("auto", {})
    assert "OPENAI_API_KEY" in result
    assert "ANTHROPIC_API_KEY" in result
    assert "GH_TOKEN" in result
    assert "PATH" not in result
    assert "RANDOM_VAR" not in result


def test_all_mode():
    """All mode passes everything except NEVER_PASSTHROUGH."""
    with patch.dict(os.environ, _fake_env(), clear=True):
        result = resolve_env_passthrough("all", {})
    assert "OPENAI_API_KEY" in result
    assert "RANDOM_VAR" in result
    assert "PATH" not in result
    assert "HOME" not in result


def test_none_mode():
    """None mode: only explicit extra_env returned."""
    with patch.dict(os.environ, _fake_env(), clear=True):
        result = resolve_env_passthrough("none", {"MY_CUSTOM": "val"})
    assert result == {"MY_CUSTOM": "val"}


def test_explicit_list_mode():
    """Only named vars from host env."""
    with patch.dict(os.environ, _fake_env(), clear=True):
        result = resolve_env_passthrough(["OPENAI_API_KEY", "RANDOM_VAR"], {})
    assert "OPENAI_API_KEY" in result
    assert "RANDOM_VAR" in result
    assert "ANTHROPIC_API_KEY" not in result
    assert len(result) == 2


def test_explicit_env_overrides():
    """extra_env wins on conflict with matched vars."""
    with patch.dict(os.environ, _fake_env(), clear=True):
        result = resolve_env_passthrough("auto", {"OPENAI_API_KEY": "override-val"})
    assert result["OPENAI_API_KEY"] == "override-val"
