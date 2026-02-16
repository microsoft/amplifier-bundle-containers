"""Tests for git config flattening in ContainerProvisioner.provision_git."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_tool_containers.provisioner import ContainerProvisioner


@dataclass
class FakeResult:
    """Minimal stand-in for a subprocess / runtime result."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _make_provisioner() -> tuple[ContainerProvisioner, AsyncMock]:
    """Create a provisioner with a mocked runtime."""
    runtime = MagicMock()
    runtime.run = AsyncMock(return_value=FakeResult(returncode=0, stdout="/root\n", stderr=""))
    return ContainerProvisioner(runtime), runtime.run


def _fake_git_process(config_text: str) -> AsyncMock:
    """Return an async context that replaces asyncio.create_subprocess_exec.

    The mock process returns *config_text* as stdout.
    """
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(config_text.encode(), b""))
    return proc


def _extract_heredoc(runtime_run_mock: AsyncMock) -> str:
    """Pull the heredoc content out of the runtime.run call that writes .gitconfig.

    The write call looks like:
        runtime.run("exec", container, "/bin/sh", "-c", "cat > ... << 'AMPLIFIER_GITCONFIG_EOF'\n...\nAMPLIFIER_GITCONFIG_EOF")
    """
    for call in runtime_run_mock.call_args_list:
        args = call[0]  # positional args
        if len(args) >= 5 and "AMPLIFIER_GITCONFIG_EOF" in str(args[4]):
            shell_cmd: str = args[4]
            # Content is between the first newline and the final EOF marker
            start = shell_cmd.index("\n") + 1
            end = shell_cmd.rindex("\nAMPLIFIER_GITCONFIG_EOF")
            return shell_cmd[start:end]
    raise AssertionError("No heredoc write call found in runtime.run calls")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_part_keys_produce_simple_sections() -> None:
    """2-part keys like user.name should produce [user] sections."""
    provisioner, run_mock = _make_provisioner()

    git_output = "user.name=Ben\nuser.email=ben@example.com\n"

    with patch("asyncio.create_subprocess_exec", return_value=_fake_git_process(git_output)):
        result = await provisioner.provision_git("test-container")

    assert result.status == "success"
    heredoc = _extract_heredoc(run_mock)
    assert "[user]" in heredoc
    assert "\tname = Ben" in heredoc
    assert "\temail = ben@example.com" in heredoc


@pytest.mark.asyncio
async def test_three_part_keys_produce_subsections() -> None:
    """3-part keys like difftool.meld.cmd should produce [difftool "meld"] sections."""
    provisioner, run_mock = _make_provisioner()

    git_output = (
        "difftool.prompt=false\n"
        'difftool.meld.cmd="C:/Program Files (x86)/Meld/meld.exe" $LOCAL $REMOTE\n'
    )

    with patch("asyncio.create_subprocess_exec", return_value=_fake_git_process(git_output)):
        result = await provisioner.provision_git("test-container")

    assert result.status == "success"
    heredoc = _extract_heredoc(run_mock)

    # 2-part key: plain section
    assert "[difftool]" in heredoc
    assert "\tprompt = false" in heredoc

    # 3-part key: subsection in double quotes
    assert '[difftool "meld"]' in heredoc
    assert "\tcmd = " in heredoc

    # Must NOT have the old broken format with dotted subkey
    assert "\tmeld.cmd = " not in heredoc


@pytest.mark.asyncio
async def test_color_subsections() -> None:
    """color.diff.frag and color.status.added should produce subsections."""
    provisioner, run_mock = _make_provisioner()

    git_output = "color.diff.frag=cyan\ncolor.status.added=green\n"

    with patch("asyncio.create_subprocess_exec", return_value=_fake_git_process(git_output)):
        result = await provisioner.provision_git("test-container")

    assert result.status == "success"
    heredoc = _extract_heredoc(run_mock)

    assert '[color "diff"]' in heredoc
    assert "\tfrag = cyan" in heredoc
    assert '[color "status"]' in heredoc
    assert "\tadded = green" in heredoc


@pytest.mark.asyncio
async def test_url_insteadof_with_dots_in_subsection() -> None:
    """url.https://github.com/.insteadOf should keep dots inside the subsection."""
    provisioner, run_mock = _make_provisioner()

    git_output = "url.https://github.com/.insteadOf=gh:\n"

    with patch("asyncio.create_subprocess_exec", return_value=_fake_git_process(git_output)):
        result = await provisioner.provision_git("test-container")

    assert result.status == "success"
    heredoc = _extract_heredoc(run_mock)

    assert '[url "https://github.com/"]' in heredoc
    assert "\tinsteadOf = gh:" in heredoc


@pytest.mark.asyncio
async def test_blocked_sections_still_filtered() -> None:
    """Blocked sections (credential, include, etc.) should still be filtered out."""
    provisioner, run_mock = _make_provisioner()

    git_output = "user.name=Ben\ncredential.helper=store\ninclude.path=~/.gitconfig.local\n"

    with patch("asyncio.create_subprocess_exec", return_value=_fake_git_process(git_output)):
        result = await provisioner.provision_git("test-container")

    assert result.status == "success"
    heredoc = _extract_heredoc(run_mock)

    assert "[user]" in heredoc
    assert "\tname = Ben" in heredoc
    # Blocked sections must not appear
    assert "[credential]" not in heredoc
    assert "[include]" not in heredoc
