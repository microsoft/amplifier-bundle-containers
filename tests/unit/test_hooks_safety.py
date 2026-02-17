"""Tests for ContainerSafetyHooks — signature compliance and policy logic."""

from __future__ import annotations

import sys
from pathlib import Path

# Add hook module to sys.path for test discovery
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "modules" / "hooks-container-safety"))

import pytest
from amplifier_core.models import HookResult

from amplifier_module_hooks_container_safety import ContainerSafetyHooks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_pre_data(operation: str = "create", **tool_input_overrides) -> dict:
    """Build a tool:pre event data dict for the containers tool."""
    tool_input = {"operation": operation, **tool_input_overrides}
    return {"tool_name": "containers", "tool_input": tool_input}


def _tool_post_data(
    operation: str = "create",
    tool_output: dict | None = None,
    **tool_input_overrides,
) -> dict:
    """Build a tool:post event data dict."""
    tool_input = {"operation": operation, **tool_input_overrides}
    return {
        "tool_name": "containers",
        "tool_input": tool_input,
        "tool_output": tool_output or {},
    }


# ---------------------------------------------------------------------------
# Signature compliance — the core fix this PR addresses
# ---------------------------------------------------------------------------


class TestSignatureCompliance:
    """Verify all handlers accept (event: str, data: dict) and return HookResult."""

    @pytest.mark.asyncio
    async def test_handle_tool_pre_accepts_event_str_and_data_dict(self):
        hooks = ContainerSafetyHooks()
        result = await hooks.handle_tool_pre("tool:pre", {"tool_name": "other"})
        assert isinstance(result, HookResult)
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_handle_tool_post_accepts_event_str_and_data_dict(self):
        hooks = ContainerSafetyHooks()
        result = await hooks.handle_tool_post("tool:post", {"tool_name": "other"})
        assert isinstance(result, HookResult)
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_handle_session_end_accepts_event_str_and_data_dict(self):
        hooks = ContainerSafetyHooks()
        result = await hooks.handle_session_end("session:end", {})
        assert isinstance(result, HookResult)
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_old_single_arg_signature_raises_type_error(self):
        """Calling with the OLD (broken) single-dict signature must fail."""
        hooks = ContainerSafetyHooks()
        with pytest.raises(TypeError):
            await hooks.handle_tool_pre({"tool_name": "containers"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tool:pre — policy enforcement
# ---------------------------------------------------------------------------


class TestToolPrePolicies:
    """Verify approval gates, deny, and continue for tool:pre."""

    @pytest.mark.asyncio
    async def test_non_containers_tool_continues(self):
        hooks = ContainerSafetyHooks()
        result = await hooks.handle_tool_pre("tool:pre", {"tool_name": "bash"})
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_safe_create_continues(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", purpose="python")
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_gpu_requests_approval(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", gpu=True)
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "ask_user"
        assert result.approval_prompt is not None
        assert "GPU" in result.approval_prompt

    @pytest.mark.asyncio
    async def test_host_network_requests_approval(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", network="host")
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "ask_user"
        assert "network" in result.approval_prompt.lower()

    @pytest.mark.asyncio
    async def test_sensitive_mount_requests_approval(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", mounts=[{"host": "/etc"}])
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "ask_user"
        assert "/etc" in result.approval_prompt

    @pytest.mark.asyncio
    async def test_non_sensitive_mount_continues(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", mounts=[{"host": "/home/user/projects/app"}])
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_ssh_forwarding_requests_approval(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", forward_ssh=True)
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "ask_user"
        assert "SSH" in result.approval_prompt

    @pytest.mark.asyncio
    async def test_all_env_passthrough_requests_approval(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", env_passthrough="all")
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "ask_user"
        assert "environment" in result.approval_prompt.lower()

    @pytest.mark.asyncio
    async def test_destroy_all_requests_approval(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("destroy_all")
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "ask_user"
        assert "ALL" in result.approval_prompt

    @pytest.mark.asyncio
    async def test_container_limit_denies(self):
        hooks = ContainerSafetyHooks(config={"max_containers_per_session": 2})
        hooks._session_containers = ["c1", "c2"]
        data = _tool_pre_data("create")
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "deny"
        assert result.reason is not None
        assert "limit" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_multiple_reasons_combined(self):
        hooks = ContainerSafetyHooks()
        data = _tool_pre_data("create", gpu=True, forward_ssh=True)
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "ask_user"
        assert "GPU" in result.approval_prompt
        assert "SSH" in result.approval_prompt

    @pytest.mark.asyncio
    async def test_custom_approval_config_skips_gpu(self):
        hooks = ContainerSafetyHooks(config={"require_approval_for": []})
        data = _tool_pre_data("create", gpu=True)
        result = await hooks.handle_tool_pre("tool:pre", data)
        assert result.action == "continue"


# ---------------------------------------------------------------------------
# tool:post — container tracking
# ---------------------------------------------------------------------------


class TestToolPostTracking:
    """Verify container tracking in tool:post."""

    @pytest.mark.asyncio
    async def test_tracks_created_container(self):
        hooks = ContainerSafetyHooks()
        data = _tool_post_data("create", tool_output={"container": "my-container", "success": True})
        result = await hooks.handle_tool_post("tool:post", data)
        assert result.action == "continue"
        assert "my-container" in hooks._session_containers

    @pytest.mark.asyncio
    async def test_does_not_track_failed_create(self):
        hooks = ContainerSafetyHooks()
        data = _tool_post_data("create", tool_output={"container": "bad", "success": False})
        await hooks.handle_tool_post("tool:post", data)
        assert "bad" not in hooks._session_containers

    @pytest.mark.asyncio
    async def test_tracks_destroyed_container(self):
        hooks = ContainerSafetyHooks()
        hooks._session_containers = ["c1", "c2"]
        data = _tool_post_data("destroy", tool_output={"success": True}, container="c1")
        await hooks.handle_tool_post("tool:post", data)
        assert "c1" not in hooks._session_containers
        assert "c2" in hooks._session_containers

    @pytest.mark.asyncio
    async def test_destroy_all_clears_tracking(self):
        hooks = ContainerSafetyHooks()
        hooks._session_containers = ["c1", "c2", "c3"]
        data = _tool_post_data("destroy_all", tool_output={"success": True})
        await hooks.handle_tool_post("tool:post", data)
        assert hooks._session_containers == []

    @pytest.mark.asyncio
    async def test_non_containers_tool_ignored(self):
        hooks = ContainerSafetyHooks()
        data = {"tool_name": "bash", "tool_input": {}, "tool_output": {}}
        result = await hooks.handle_tool_post("tool:post", data)
        assert result.action == "continue"


# ---------------------------------------------------------------------------
# session:end — cleanup
# ---------------------------------------------------------------------------


class TestSessionEndCleanup:
    """Verify session cleanup behavior."""

    @pytest.mark.asyncio
    async def test_no_containers_continues(self):
        hooks = ContainerSafetyHooks()
        result = await hooks.handle_session_end("session:end", {})
        assert result.action == "continue"
        assert result.data is None

    @pytest.mark.asyncio
    async def test_cleanup_emits_container_list(self):
        hooks = ContainerSafetyHooks()
        hooks._session_containers = ["c1", "c2"]
        result = await hooks.handle_session_end("session:end", {})
        assert result.action == "continue"
        assert result.data is not None
        assert result.data["cleanup_containers"] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_auto_cleanup_disabled_skips(self):
        hooks = ContainerSafetyHooks(config={"auto_cleanup_on_session_end": False})
        hooks._session_containers = ["c1"]
        result = await hooks.handle_session_end("session:end", {})
        assert result.data is None


# ---------------------------------------------------------------------------
# _is_sensitive_path
# ---------------------------------------------------------------------------


class TestSensitivePaths:
    """Verify sensitive mount path detection."""

    def test_root_is_sensitive(self):
        hooks = ContainerSafetyHooks()
        assert hooks._is_sensitive_path("/") is True

    def test_etc_is_sensitive(self):
        hooks = ContainerSafetyHooks()
        assert hooks._is_sensitive_path("/etc") is True

    def test_home_is_sensitive(self):
        hooks = ContainerSafetyHooks()
        assert hooks._is_sensitive_path("/home") is True

    def test_subdirectory_of_home_is_not_sensitive(self):
        hooks = ContainerSafetyHooks()
        assert hooks._is_sensitive_path("/home/user/projects") is False

    def test_empty_path_is_not_sensitive(self):
        hooks = ContainerSafetyHooks()
        assert hooks._is_sensitive_path("") is False

    def test_trailing_slash_normalized(self):
        hooks = ContainerSafetyHooks()
        assert hooks._is_sensitive_path("/etc/") is True
