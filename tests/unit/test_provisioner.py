"""Tests for environment variable matching, passthrough logic, and container provisioning."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from amplifier_module_tool_containers.provisioner import (
    ContainerProvisioner,
    ProvisioningStep,
    match_env_patterns,
    resolve_env_passthrough,
)
from amplifier_module_tool_containers.runtime import CommandResult, ContainerRuntime


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


# ---------------------------------------------------------------------------
# ContainerProvisioner
# ---------------------------------------------------------------------------


def _make_provisioner(run_side_effect=None):
    """Create a ContainerProvisioner with a mocked runtime."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    if run_side_effect is not None:
        runtime.run = AsyncMock(side_effect=run_side_effect)
    else:
        runtime.run = AsyncMock(return_value=CommandResult(0, "", ""))
    return ContainerProvisioner(runtime)


@pytest.mark.asyncio
async def test_get_container_home_returns_home():
    """get_container_home returns the HOME env var from the container."""
    prov = _make_provisioner()
    prov.runtime.run = AsyncMock(return_value=CommandResult(0, "/home/user\n", ""))
    home = await prov.get_container_home("mycontainer")
    assert home == "/home/user"
    prov.runtime.run.assert_called_once_with(
        "exec", "mycontainer", "/bin/sh", "-c", "echo $HOME", timeout=5
    )


@pytest.mark.asyncio
async def test_get_container_home_fallback_root():
    """get_container_home falls back to /root when HOME is empty."""
    prov = _make_provisioner()
    prov.runtime.run = AsyncMock(return_value=CommandResult(0, "\n", ""))
    home = await prov.get_container_home("mycontainer")
    assert home == "/root"


@pytest.mark.asyncio
async def test_fix_ssh_copies_from_staging():
    """fix_ssh_permissions copies from /tmp/.host-ssh to container home .ssh."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/devuser\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    await prov.fix_ssh_permissions("c1")

    # First call fetches $HOME
    assert calls[0] == ("exec", "c1", "/bin/sh", "-c", "echo $HOME")
    # Remaining calls operate on /home/devuser/.ssh
    # Args are: ("exec", "c1", "/bin/sh", "-c", "<shell command>")
    shell_cmds = [c[4] for c in calls[1:] if len(c) > 4 and c[3] == "-c"]
    assert any("/home/devuser/.ssh" in cmd for cmd in shell_cmds)
    assert any("/tmp/.host-ssh" in cmd for cmd in shell_cmds)
    # No /root/ references in any command
    for cmd in shell_cmds:
        assert "/root/" not in cmd


@pytest.mark.asyncio
async def test_provision_git_uses_dynamic_home():
    """provision_git targets the container's $HOME, not /root."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/builder\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    # Create fake gitconfig so the copy logic triggers
    with patch("amplifier_module_tool_containers.provisioner.Path") as mock_path:
        mock_home = mock_path.home.return_value
        mock_gitconfig = mock_home.__truediv__.return_value
        mock_gitconfig.exists.return_value = True
        mock_gitconfig.__str__ = lambda self: "/fakehome/.gitconfig"

        await prov.provision_git("c1")

    # First call is get_container_home
    assert calls[0] == ("exec", "c1", "/bin/sh", "-c", "echo $HOME")
    # Verify no /root/ in any call
    for call in calls:
        assert all("/root/" not in arg for arg in call)


# ---------------------------------------------------------------------------
# UID/GID mapping in _op_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uid_gid_mapping_default():
    """When mount_cwd=True, --user flag is added with host UID:GID."""
    from amplifier_module_tool_containers import ContainersTool

    tool = ContainersTool()
    tool._preflight_passed = True

    captured_args: list[str] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.extend(args)
        # Return container ID for the "run" call, empty for provisioning
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    uid = os.getuid()
    gid = os.getgid()

    await tool.execute(
        "containers",
        {
            "operation": "create",
            "name": "test-uid",
            "mount_cwd": True,
            "forward_git": False,
            "forward_gh": False,
        },
    )

    assert "--user" in captured_args
    idx = captured_args.index("--user")
    assert captured_args[idx + 1] == f"{uid}:{gid}"


@pytest.mark.asyncio
async def test_uid_gid_mapping_no_mount():
    """When mount_cwd=False and no mounts, no --user flag added."""
    from amplifier_module_tool_containers import ContainersTool

    tool = ContainersTool()
    tool._preflight_passed = True

    captured_args: list[str] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.extend(args)
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        "containers",
        {
            "operation": "create",
            "name": "test-nouid",
            "mount_cwd": False,
            "mounts": [],
            "forward_git": False,
            "forward_gh": False,
        },
    )

    assert "--user" not in captured_args


@pytest.mark.asyncio
async def test_uid_gid_mapping_explicit_root():
    """user='root' does NOT add --user flag."""
    from amplifier_module_tool_containers import ContainersTool

    tool = ContainersTool()
    tool._preflight_passed = True

    captured_args: list[str] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.extend(args)
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        "containers",
        {
            "operation": "create",
            "name": "test-root",
            "user": "root",
            "mount_cwd": True,
            "forward_git": False,
            "forward_gh": False,
        },
    )

    assert "--user" not in captured_args


@pytest.mark.asyncio
async def test_uid_gid_mapping_explicit_user():
    """user='1000:1000' is used as-is."""
    from amplifier_module_tool_containers import ContainersTool

    tool = ContainersTool()
    tool._preflight_passed = True

    captured_args: list[str] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.extend(args)
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        "containers",
        {
            "operation": "create",
            "name": "test-explicit",
            "user": "1000:1000",
            "mount_cwd": True,
            "forward_git": False,
            "forward_gh": False,
        },
    )

    assert "--user" in captured_args
    idx = captured_args.index("--user")
    assert captured_args[idx + 1] == "1000:1000"


# ---------------------------------------------------------------------------
# ProvisioningStep returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_git_success_returns_step():
    """provision_git returns ProvisioningStep with success status."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/user\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    with patch("amplifier_module_tool_containers.provisioner.Path") as mock_path:
        mock_home = mock_path.home.return_value
        # Make .gitconfig exist
        gitconfig_mock = type(
            "FP", (), {"exists": lambda self: True, "__str__": lambda self: "/fakehome/.gitconfig"}
        )()
        gitconfig_local_mock = type(
            "FP",
            (),
            {"exists": lambda self: False, "__str__": lambda self: "/fakehome/.gitconfig.local"},
        )()
        known_hosts_mock = type(
            "FP",
            (),
            {"exists": lambda self: False, "__str__": lambda self: "/fakehome/.ssh/known_hosts"},
        )()

        def _truediv(self, key):
            if key == ".gitconfig":
                return gitconfig_mock
            if key == ".gitconfig.local":
                return gitconfig_local_mock
            if key == ".ssh/known_hosts":
                return known_hosts_mock
            return type(
                "FP", (), {"exists": lambda s: False, "__str__": lambda s: f"/fakehome/{key}"}
            )()

        mock_home.__truediv__ = _truediv

        step = await prov.provision_git("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_git"
    assert step.status == "success"
    assert ".gitconfig" in step.detail
    assert step.error is None


@pytest.mark.asyncio
async def test_provision_git_skipped_no_config():
    """provision_git returns skipped when no .gitconfig exists."""
    prov = _make_provisioner()

    with patch("amplifier_module_tool_containers.provisioner.Path") as mock_path:
        mock_home = mock_path.home.return_value
        # .gitconfig does not exist
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fakehome/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        step = await prov.provision_git("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_git"
    assert step.status == "skipped"
    assert "No .gitconfig" in step.detail


@pytest.mark.asyncio
async def test_provision_gh_skipped_no_cli():
    """provision_gh_auth returns skipped when gh CLI not found."""
    prov = _make_provisioner()

    with patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None):
        step = await prov.provision_gh_auth("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_gh"
    assert step.status == "skipped"
    assert "not found" in step.detail


@pytest.mark.asyncio
async def test_provision_gh_skipped_not_authenticated():
    """provision_gh_auth returns skipped when gh auth token fails."""
    prov = _make_provisioner()

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.shutil.which", return_value="/usr/bin/gh"
        ),
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_proc,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"not logged in")
        proc.returncode = 1
        mock_proc.return_value = proc

        step = await prov.provision_gh_auth("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_gh"
    assert step.status == "skipped"
    assert "not authenticated" in step.detail


@pytest.mark.asyncio
async def test_fix_ssh_returns_success_step():
    """fix_ssh_permissions returns ProvisioningStep with success status."""
    prov = _make_provisioner()
    prov.runtime.run = AsyncMock(return_value=CommandResult(0, "/home/user\n", ""))

    step = await prov.fix_ssh_permissions("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_ssh"
    assert step.status == "success"
    assert "SSH keys" in step.detail


@pytest.mark.asyncio
async def test_fix_ssh_returns_failed_step():
    """fix_ssh_permissions returns failed when a command errors."""
    call_count = 0

    async def _fail_on_second(*args: str, **kwargs: object) -> CommandResult:
        nonlocal call_count
        call_count += 1
        # First call is get_container_home, second is mkdir
        if call_count <= 2:
            return CommandResult(0, "/home/user\n", "")
        return CommandResult(1, "", "permission denied")

    prov = _make_provisioner()
    prov.runtime.run = _fail_on_second  # type: ignore[assignment]

    step = await prov.fix_ssh_permissions("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_ssh"
    assert step.status == "failed"
    assert step.error is not None
