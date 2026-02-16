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

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_exec,
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (b"user.name=Test\n", b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        mock_home = mock_path.home.return_value
        mock_home.__truediv__ = lambda self, key: type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: f"/fakehome/{key}"}
        )()

        await prov.provision_git("c1")

    # First call is get_container_home
    assert calls[0] == ("exec", "c1", "/bin/sh", "-c", "echo $HOME")
    # Heredoc write targets /home/builder/.gitconfig
    heredoc_calls = [c for c in calls if len(c) > 4 and "cat >" in str(c[4])]
    assert any("/home/builder/.gitconfig" in str(c[4]) for c in heredoc_calls)
    # Verify no /root/ in any call
    for call in calls:
        assert all("/root/" not in str(arg) for arg in call)


# ---------------------------------------------------------------------------
# Two-phase user model: exec_user in metadata (not --user on docker run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uid_gid_mapping_default(tmp_path):
    """When mount_cwd=True, exec_user is stored in metadata with host UID:GID."""
    from amplifier_module_tool_containers import ContainersTool, MetadataStore

    tool = ContainersTool()
    tool._preflight_passed = True
    tool.store = MetadataStore(base_dir=tmp_path)

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    uid = os.getuid()
    gid = os.getgid()

    await tool.execute(
        {
            "operation": "create",
            "name": "test-uid",
            "mount_cwd": True,
            "forward_git": False,
            "forward_gh": False,
        },
    )

    metadata = tool.store.load("test-uid")
    assert metadata is not None
    assert metadata["exec_user"] == f"{uid}:{gid}"


@pytest.mark.asyncio
async def test_uid_gid_mapping_no_mount(tmp_path):
    """When mount_cwd=False and no mounts, no exec_user in metadata."""
    from amplifier_module_tool_containers import ContainersTool, MetadataStore

    tool = ContainersTool()
    tool._preflight_passed = True
    tool.store = MetadataStore(base_dir=tmp_path)

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        {
            "operation": "create",
            "name": "test-nouid",
            "mount_cwd": False,
            "mounts": [],
            "forward_git": False,
            "forward_gh": False,
        },
    )

    metadata = tool.store.load("test-nouid")
    assert metadata is not None
    assert metadata["exec_user"] is None


@pytest.mark.asyncio
async def test_uid_gid_mapping_explicit_root(tmp_path):
    """user='root' results in no exec_user in metadata."""
    from amplifier_module_tool_containers import ContainersTool, MetadataStore

    tool = ContainersTool()
    tool._preflight_passed = True
    tool.store = MetadataStore(base_dir=tmp_path)

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        {
            "operation": "create",
            "name": "test-root",
            "user": "root",
            "mount_cwd": True,
            "forward_git": False,
            "forward_gh": False,
        },
    )

    metadata = tool.store.load("test-root")
    assert metadata is not None
    assert metadata["exec_user"] is None


@pytest.mark.asyncio
async def test_uid_gid_mapping_explicit_user(tmp_path):
    """user='1000:1000' is stored as exec_user in metadata."""
    from amplifier_module_tool_containers import ContainersTool, MetadataStore

    tool = ContainersTool()
    tool._preflight_passed = True
    tool.store = MetadataStore(base_dir=tmp_path)

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        {
            "operation": "create",
            "name": "test-explicit",
            "user": "1000:1000",
            "mount_cwd": True,
            "forward_git": False,
            "forward_gh": False,
        },
    )

    metadata = tool.store.load("test-explicit")
    assert metadata is not None
    assert metadata["exec_user"] == "1000:1000"


# ---------------------------------------------------------------------------
# ProvisioningStep returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_git_uses_includes_flag():
    """provision_git passes --includes so git resolves [include]/[includeIf] chains."""
    prov = _make_provisioner()
    prov.runtime.run = AsyncMock(return_value=CommandResult(0, "/home/user\n", ""))

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_exec,
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (b"user.name=Test\n", b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        mock_home = mock_path.home.return_value
        mock_home.__truediv__ = lambda self, key: type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: f"/fakehome/{key}"}
        )()

        await prov.provision_git("c1")

    # Verify create_subprocess_exec was called with --includes
    mock_exec.assert_called_once()
    call_args = mock_exec.call_args[0]
    assert "--includes" in call_args, f"Expected '--includes' in subprocess args, got: {call_args}"


@pytest.mark.asyncio
async def test_provision_git_success_returns_step():
    """provision_git returns ProvisioningStep with success status."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/user\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    git_output = b"user.name=Ben Krabach\nuser.email=ben@example.com\nalias.co=checkout\n"

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_exec,
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (git_output, b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        mock_home = mock_path.home.return_value
        mock_home.__truediv__ = lambda self, key: type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: f"/fakehome/{key}"}
        )()

        step = await prov.provision_git("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_git"
    assert step.status == "success"
    assert "3 settings" in step.detail
    assert step.error is None

    # Verify --includes flag is passed to resolve [include]/[includeIf] chains
    mock_exec.assert_called_once()
    assert "--includes" in mock_exec.call_args[0]


@pytest.mark.asyncio
async def test_provision_git_skipped_no_config():
    """provision_git returns skipped when host has no git config."""
    prov = _make_provisioner()

    with patch(
        "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
    ) as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"")
        proc.returncode = 1
        mock_exec.return_value = proc

        step = await prov.provision_git("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_git"
    assert step.status == "skipped"
    assert "No git config" in step.detail


@pytest.mark.asyncio
async def test_provision_git_skipped_empty_output():
    """provision_git returns skipped when git config returns empty output."""
    prov = _make_provisioner()

    with patch(
        "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
    ) as mock_exec:
        proc = AsyncMock()
        proc.communicate.return_value = (b"\n", b"")
        proc.returncode = 0
        mock_exec.return_value = proc

        step = await prov.provision_git("c1")

    assert step.status == "skipped"
    assert "No git config" in step.detail


@pytest.mark.asyncio
async def test_provision_git_skipped_oserror():
    """provision_git returns skipped when git is not installed on host."""
    prov = _make_provisioner()

    with patch(
        "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec",
        side_effect=OSError("No such file or directory"),
    ):
        step = await prov.provision_git("c1")

    assert step.name == "forward_git"
    assert step.status == "skipped"
    assert "No git config" in step.detail


@pytest.mark.asyncio
async def test_provision_git_filters_blocked_sections():
    """provision_git excludes credential, include, includeIf, http, safe sections."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/user\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    git_output = (
        b"user.name=Test User\n"
        b"user.email=test@example.com\n"
        b"credential.helper=!/usr/bin/gh auth git-credential\n"
        b"include.path=~/.gitconfig.local\n"
        b"includeif.gitdir:~/work/.path=~/.gitconfig-work\n"
        b"http.proxy=http://proxy:8080\n"
        b"safe.directory=/some/path\n"
        b"alias.co=checkout\n"
    )

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_exec,
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (git_output, b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        mock_home = mock_path.home.return_value
        mock_home.__truediv__ = lambda self, key: type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: f"/fakehome/{key}"}
        )()

        step = await prov.provision_git("c1")

    assert step.status == "success"
    assert "3 settings" in step.detail
    assert "filtered" in step.detail

    # Verify blocked content NOT in the heredoc
    heredoc_calls = [c for c in calls if len(c) > 4 and "cat >" in str(c[4])]
    assert len(heredoc_calls) == 1
    written = heredoc_calls[0][4]
    assert "[credential]" not in written
    assert "[include]" not in written
    assert "[includeif]" not in written
    assert "[http]" not in written
    assert "[safe]" not in written
    # Allowed content IS present
    assert "[user]" in written
    assert "name = Test User" in written
    assert "[alias]" in written
    assert "co = checkout" in written


@pytest.mark.asyncio
async def test_provision_git_special_characters_in_values():
    """provision_git handles values with =, quotes, and multi-dot keys."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/user\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    git_output = (
        b"user.name=O'Brien\n"
        b"url.https://github.com/.insteadof=gh:\n"
        b'user.signingkey=A "Special" Key\n'
        b"core.pager=less -R\n"
    )

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_exec,
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (git_output, b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        mock_home = mock_path.home.return_value
        mock_home.__truediv__ = lambda self, key: type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: f"/fakehome/{key}"}
        )()

        step = await prov.provision_git("c1")

    assert step.status == "success"
    assert "4 settings" in step.detail

    heredoc_calls = [c for c in calls if len(c) > 4 and "cat >" in str(c[4])]
    written = heredoc_calls[0][4]
    # Value with = in URL preserved correctly (partition splits on first = only)
    assert "https://github.com/.insteadof = gh:" in written
    # Single quotes don't need escaping in gitconfig
    assert "name = O'Brien" in written
    # Value with double quotes gets escaped and wrapped
    assert 'signingkey = "A \\"Special\\" Key"' in written
    # Normal value stays plain
    assert "pager = less -R" in written


@pytest.mark.asyncio
async def test_provision_git_copies_supplementary_files():
    """provision_git still copies .gitconfig.local and .ssh/known_hosts."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/user\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_exec,
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (b"user.name=Test\n", b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        mock_home = mock_path.home.return_value

        def _truediv(self, key):
            exists = key in (".gitconfig.local", ".ssh/known_hosts")
            return type(
                "FP",
                (),
                {"exists": lambda self: exists, "__str__": lambda self: f"/fakehome/{key}"},
            )()

        mock_home.__truediv__ = _truediv

        step = await prov.provision_git("c1")

    assert step.status == "success"
    assert ".gitconfig.local" in step.detail
    assert ".ssh/known_hosts" in step.detail
    # Verify docker cp was called for both supplementary files
    cp_calls = [c for c in calls if c[0] == "cp"]
    assert len(cp_calls) == 2


@pytest.mark.asyncio
async def test_provision_git_all_entries_filtered():
    """provision_git writes empty config when all entries are blocked."""
    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/user\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    git_output = (
        b"credential.helper=osxkeychain\n"
        b"http.proxy=http://proxy:8080\n"
        b"safe.directory=/opt/project\n"
    )

    with (
        patch(
            "amplifier_module_tool_containers.provisioner.asyncio.create_subprocess_exec"
        ) as mock_exec,
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        proc = AsyncMock()
        proc.communicate.return_value = (git_output, b"")
        proc.returncode = 0
        mock_exec.return_value = proc
        mock_home = mock_path.home.return_value
        mock_home.__truediv__ = lambda self, key: type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: f"/fakehome/{key}"}
        )()

        step = await prov.provision_git("c1")

    assert step.status == "success"
    assert "0 settings" in step.detail
    assert "filtered" in step.detail


@pytest.mark.asyncio
async def test_provision_gh_skipped_no_token():
    """provision_gh_auth returns skipped when no gh_env_vars provided."""
    prov = _make_provisioner()

    step = await prov.provision_gh_auth("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_gh"
    assert step.status == "skipped"
    assert "No GH token" in step.detail


@pytest.mark.asyncio
async def test_provision_gh_skipped_empty_env_vars():
    """provision_gh_auth returns skipped when gh_env_vars is empty dict."""
    prov = _make_provisioner()

    step = await prov.provision_gh_auth("c1", gh_env_vars={})

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_gh"
    assert step.status == "skipped"
    assert "No GH token" in step.detail


@pytest.mark.asyncio
async def test_provision_gh_verified_in_container():
    """provision_gh_auth verifies token is visible and reports success."""
    token = "ghp_test123"

    async def _mock_run(*args, **kwargs):
        cmd_str = " ".join(str(a) for a in args)
        if "printenv GH_TOKEN" in cmd_str:
            return CommandResult(0, token + "\n", "")
        if "which" in cmd_str and "gh" in cmd_str:
            return CommandResult(1, "", "")  # No gh CLI in container
        return CommandResult(0, "", "")

    prov = _make_provisioner()
    prov.runtime.run = _mock_run  # type: ignore[assignment]

    step = await prov.provision_gh_auth(
        "c1", gh_env_vars={"GH_TOKEN": token, "GITHUB_TOKEN": token}
    )

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_gh"
    assert step.status == "success"
    assert "verified" in step.detail


@pytest.mark.asyncio
async def test_provision_gh_auth_login_success_with_gh_cli():
    """When gh CLI is in the container and login succeeds, detail includes 'gh auth login completed'."""
    token = "ghp_test123"
    commands_run: list[tuple[object, ...]] = []

    async def _mock_run(*args, **kwargs):
        commands_run.append(args)
        cmd_str = " ".join(str(a) for a in args)
        if "gh auth login" in cmd_str:
            return CommandResult(0, "", "")  # login succeeds
        if "printenv GH_TOKEN" in cmd_str:
            return CommandResult(0, token + "\n", "")  # verify succeeds
        if "which" in cmd_str:
            return CommandResult(0, "/usr/bin/gh\n", "")  # gh CLI IS present
        return CommandResult(0, "", "")

    prov = _make_provisioner()
    prov.runtime.run = _mock_run  # type: ignore[assignment]

    step = await prov.provision_gh_auth(
        "c1", gh_env_vars={"GH_TOKEN": token, "GITHUB_TOKEN": token}
    )

    assert step.status == "success"
    assert "gh auth login completed" in step.detail

    # Security: the login command must NOT interpolate the raw token
    login_cmds = [
        " ".join(str(a) for a in c)
        for c in commands_run
        if any("gh auth login" in str(a) for a in c)
    ]
    assert len(login_cmds) == 1
    assert token not in login_cmds[0]  # should use printenv, not echo with token


@pytest.mark.asyncio
async def test_provision_gh_auth_login_failure_with_gh_cli():
    """When gh CLI is in the container but login fails, detail includes 'gh auth login failed'."""
    token = "ghp_test123"

    async def _mock_run(*args, **kwargs):
        cmd_str = " ".join(str(a) for a in args)
        if "gh auth login" in cmd_str:
            return CommandResult(1, "", "auth error")  # login FAILS
        if "printenv GH_TOKEN" in cmd_str:
            return CommandResult(0, token + "\n", "")
        if "which" in cmd_str:
            return CommandResult(0, "/usr/bin/gh\n", "")
        return CommandResult(0, "", "")

    prov = _make_provisioner()
    prov.runtime.run = _mock_run  # type: ignore[assignment]

    step = await prov.provision_gh_auth(
        "c1", gh_env_vars={"GH_TOKEN": token, "GITHUB_TOKEN": token}
    )

    # Overall still success (token was verified), but login failed
    assert step.status == "success"
    assert "gh auth login failed" in step.detail


@pytest.mark.asyncio
async def test_provision_gh_failed_verification():
    """provision_gh_auth returns failed when token not visible in container."""
    prov = _make_provisioner()
    prov.runtime.run = AsyncMock(return_value=CommandResult(0, "\n", ""))

    step = await prov.provision_gh_auth(
        "c1", gh_env_vars={"GH_TOKEN": "ghp_test", "GITHUB_TOKEN": "ghp_test"}
    )

    assert isinstance(step, ProvisioningStep)
    assert step.name == "forward_gh"
    assert step.status == "failed"
    assert "not visible" in step.detail


# ---------------------------------------------------------------------------
# extract_gh_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_gh_token_no_cli():
    """extract_gh_token returns empty dict when gh CLI not found."""
    prov = _make_provisioner()

    with patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None):
        result = await prov.extract_gh_token()

    assert result == {}


@pytest.mark.asyncio
async def test_extract_gh_token_not_authenticated():
    """extract_gh_token returns empty dict when gh auth token fails."""
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

        result = await prov.extract_gh_token()

    assert result == {}


@pytest.mark.asyncio
async def test_extract_gh_token_success():
    """extract_gh_token returns GH_TOKEN and GITHUB_TOKEN when authenticated."""
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
        proc.communicate.return_value = (b"ghp_abc123\n", b"")
        proc.returncode = 0
        mock_proc.return_value = proc

        result = await prov.extract_gh_token()

    assert result == {"GH_TOKEN": "ghp_abc123", "GITHUB_TOKEN": "ghp_abc123"}


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
async def test_provision_amplifier_settings_success(tmp_path):
    """provision_amplifier_settings copies settings when they exist."""
    # Create fake ~/.amplifier with settings files
    amp_dir = tmp_path / ".amplifier"
    amp_dir.mkdir()
    (amp_dir / "settings.yaml").write_text("provider: anthropic\n")
    (amp_dir / "settings.local.yaml").write_text("api_key: sk-test\n")

    calls: list[tuple[str, ...]] = []

    async def _track(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(0, "/home/hostuser\n", "")

    prov = _make_provisioner()
    prov.runtime.run = _track  # type: ignore[assignment]

    with patch("amplifier_module_tool_containers.provisioner.Path") as mock_path:
        mock_path.home.return_value = tmp_path
        step = await prov.provision_amplifier_settings("c1", target_home="/home/hostuser")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "amplifier_settings"
    assert step.status == "success"
    assert "settings.yaml" in step.detail
    assert "settings.local.yaml" in step.detail


@pytest.mark.asyncio
async def test_provision_amplifier_settings_no_dir(tmp_path):
    """provision_amplifier_settings skips when no ~/.amplifier."""
    # tmp_path has no .amplifier directory
    prov = _make_provisioner()

    with patch("amplifier_module_tool_containers.provisioner.Path") as mock_path:
        mock_path.home.return_value = tmp_path
        step = await prov.provision_amplifier_settings("c1")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "amplifier_settings"
    assert step.status == "skipped"
    assert "No ~/.amplifier" in step.detail


@pytest.mark.asyncio
async def test_provision_amplifier_settings_no_files(tmp_path):
    """provision_amplifier_settings skips when ~/.amplifier has no settings files."""
    amp_dir = tmp_path / ".amplifier"
    amp_dir.mkdir()
    # Directory exists but no settings.yaml or settings.local.yaml

    prov = _make_provisioner()
    prov.runtime.run = AsyncMock(return_value=CommandResult(0, "/home/hostuser\n", ""))

    with patch("amplifier_module_tool_containers.provisioner.Path") as mock_path:
        mock_path.home.return_value = tmp_path
        step = await prov.provision_amplifier_settings("c1", target_home="/home/hostuser")

    assert isinstance(step, ProvisioningStep)
    assert step.name == "amplifier_settings"
    assert step.status == "skipped"
    assert "No settings files" in step.detail


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


# ---------------------------------------------------------------------------
# provision_repos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_repos_success():
    """provision_repos clones repos and reports success."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=0, stdout="", stderr="")

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    result = await provisioner.provision_repos(
        "test-container",
        [
            {"url": "https://github.com/user/repo1", "path": "/workspace/repo1"},
            {"url": "https://github.com/user/repo2", "path": "/workspace/repo2"},
        ],
    )
    assert result.status == "success"
    assert "2 repos" in result.detail


@pytest.mark.asyncio
async def test_provision_repos_with_install():
    """provision_repos runs install command after cloning."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    commands_run: list[tuple[object, ...]] = []

    async def _mock_run(*args, **kwargs):
        commands_run.append(args)
        return CommandResult(returncode=0, stdout="", stderr="")

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    result = await provisioner.provision_repos(
        "test-container",
        [
            {
                "url": "https://github.com/user/repo1",
                "path": "/workspace/repo1",
                "install": "pip install -e .",
            },
        ],
    )
    assert result.status == "success"
    # Should have both clone and install commands
    assert len(commands_run) == 2


@pytest.mark.asyncio
async def test_provision_repos_clone_failure():
    """provision_repos reports partial when one repo fails."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    call_count = 0

    async def _mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # First clone succeeds
            return CommandResult(returncode=0, stdout="", stderr="")
        return CommandResult(returncode=1, stdout="", stderr="fatal: not found")  # Second fails

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    result = await provisioner.provision_repos(
        "test-container",
        [
            {"url": "https://github.com/user/repo1"},
            {"url": "https://github.com/user/repo2"},
        ],
    )
    assert result.status == "partial"
    assert "1/2" in result.detail


@pytest.mark.asyncio
async def test_provision_repos_all_fail():
    """provision_repos reports failed when all repos fail."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=1, stdout="", stderr="fatal: not found")

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    result = await provisioner.provision_repos(
        "test-container",
        [
            {"url": "https://github.com/user/repo1"},
        ],
    )
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_provision_repos_empty():
    """provision_repos returns skipped for empty list."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    provisioner = ContainerProvisioner(runtime)
    result = await provisioner.provision_repos("test-container", [])
    assert result.status == "skipped"


@pytest.mark.asyncio
async def test_provision_repos_default_path():
    """provision_repos uses /workspace/{name} when path not specified."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    clone_cmd = ""

    async def _mock_run(*args, **kwargs):
        nonlocal clone_cmd
        for a in args:
            if isinstance(a, str) and "git clone" in a:
                clone_cmd = a
        return CommandResult(returncode=0, stdout="", stderr="")

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    await provisioner.provision_repos(
        "test-container",
        [
            {"url": "https://github.com/user/my-repo"},
        ],
    )
    assert "/workspace/my-repo" in clone_cmd


# ---------------------------------------------------------------------------
# provision_config_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_config_files_success():
    """provision_config_files writes files and reports success."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=0, stdout="", stderr="")

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    result = await provisioner.provision_config_files(
        "test-container",
        {
            "/workspace/.storage.yaml": "provider: git\n",
            "/workspace/.config.yaml": "debug: true\n",
        },
    )
    assert result.status == "success"
    assert "2 files" in result.detail


@pytest.mark.asyncio
async def test_provision_config_files_failure():
    """provision_config_files reports partial on failure."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    call_count = 0

    async def _mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return CommandResult(returncode=0, stdout="", stderr="")
        return CommandResult(returncode=1, stdout="", stderr="permission denied")

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    result = await provisioner.provision_config_files(
        "test-container",
        {
            "/workspace/a.yaml": "a\n",
            "/root/b.yaml": "b\n",
        },
    )
    assert result.status == "partial"


@pytest.mark.asyncio
async def test_provision_config_files_empty():
    """provision_config_files returns skipped for empty dict."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    provisioner = ContainerProvisioner(runtime)
    result = await provisioner.provision_config_files("test-container", {})
    assert result.status == "skipped"


@pytest.mark.asyncio
async def test_provision_config_files_creates_dirs():
    """provision_config_files creates parent directories."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    cmd_run = ""

    async def _mock_run(*args, **kwargs):
        nonlocal cmd_run
        for a in args:
            if isinstance(a, str) and "mkdir" in a:
                cmd_run = a
        return CommandResult(returncode=0, stdout="", stderr="")

    runtime.run = _mock_run  # type: ignore[assignment]
    provisioner = ContainerProvisioner(runtime)

    await provisioner.provision_config_files(
        "test-container",
        {
            "/workspace/deep/nested/config.yaml": "test\n",
        },
    )
    assert "mkdir -p" in cmd_run
