"""Tests for ContainersTool dispatch and high-level behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from amplifier_module_tool_containers import ContainersTool
from amplifier_module_tool_containers.runtime import CommandResult


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def test_tool_definitions_valid(tool: ContainersTool):
    """tool_definitions returns valid schema with 'containers' name."""
    defs = tool.tool_definitions
    assert isinstance(defs, list)
    assert len(defs) == 1
    defn = defs[0]
    assert defn["name"] == "containers"
    assert "input_schema" in defn
    schema = defn["input_schema"]
    assert schema["type"] == "object"
    assert "operation" in schema["properties"]
    assert "required" in schema
    assert "operation" in schema["required"]


# ---------------------------------------------------------------------------
# Dispatch errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_operation_returns_error(tool: ContainersTool):
    """execute with bad operation returns error dict."""
    result = await tool.execute("containers", {"operation": "teleport"})
    assert "error" in result
    assert "teleport" in result["error"]


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_all_pass(tool: ContainersTool):
    """Mock runtime methods to all return success."""
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "{}", ""))
    tool.runtime.is_daemon_running = AsyncMock(return_value=True)
    tool.runtime.user_has_permissions = AsyncMock(return_value=True)
    result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is True
    assert result["runtime"] == "docker"
    assert all(c["passed"] for c in result["checks"])


@pytest.mark.asyncio
async def test_preflight_no_runtime(tool: ContainersTool):
    """Mock detect() to return None."""
    tool.runtime._runtime = None  # Reset cache
    with patch("shutil.which", return_value=None):
        result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is False
    assert result["runtime"] is None


# ---------------------------------------------------------------------------
# Auto-preflight on first create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_preflight_on_first_create(tool: ContainersTool):
    """First create triggers preflight automatically; fails if runtime not ready."""
    tool._preflight_passed = False
    tool.runtime._runtime = None  # Force no runtime
    with patch("shutil.which", return_value=None):
        result = await tool.execute("containers", {"operation": "create", "name": "test"})
    assert "error" in result
    assert "not ready" in result["error"].lower()


# ---------------------------------------------------------------------------
# Exec validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_requires_container_and_command(tool: ContainersTool):
    """Returns error if missing container or command."""
    result = await tool.execute("containers", {"operation": "exec"})
    assert "error" in result
    assert "required" in result["error"].lower()

    result = await tool.execute("containers", {"operation": "exec", "container": "c1"})
    assert "error" in result

    result = await tool.execute("containers", {"operation": "exec", "command": "ls"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Destroy all validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_destroy_all_requires_confirm(tool: ContainersTool):
    """Returns error without confirm=true."""
    result = await tool.execute("containers", {"operation": "destroy_all"})
    assert "error" in result
    assert "confirm" in result["error"].lower()


# ---------------------------------------------------------------------------
# Copy in validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_in_requires_all_params(tool: ContainersTool):
    """Returns error if missing any of container/host_path/container_path."""
    result = await tool.execute(
        "containers",
        {"operation": "copy_in", "container": "c1", "host_path": "/tmp/f"},
    )
    assert "error" in result

    result = await tool.execute(
        "containers",
        {"operation": "copy_in", "host_path": "/tmp/f", "container_path": "/dst"},
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# List empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(tool: ContainersTool):
    """Returns empty list when no containers."""
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "", ""))
    result = await tool.execute("containers", {"operation": "list"})
    assert result["containers"] == []
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# Provisioning report in create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_provisioning_report(tool: ContainersTool):
    """create operation returns provisioning_report in result."""
    tool._preflight_passed = True

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    # Patch out gh CLI and gitconfig so provisioning takes known paths
    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-report",
                "forward_git": True,
                "forward_gh": True,
                "forward_ssh": False,
            },
        )

    assert "provisioning_report" in result
    report = result["provisioning_report"]
    assert isinstance(report, list)
    assert len(report) > 0
    # Each entry has the required keys
    for entry in report:
        assert "name" in entry
        assert "status" in entry
        assert "detail" in entry
        assert "error" in entry
    # Verify specific steps are present
    step_names = [e["name"] for e in report]
    assert "env_passthrough" in step_names
    assert "forward_git" in step_names
    assert "forward_gh" in step_names
    assert "forward_ssh" in step_names
    assert "dotfiles" in step_names


@pytest.mark.asyncio
async def test_provisioning_report_setup_command_partial(tool: ContainersTool):
    """When some setup_commands fail, report shows partial status."""
    tool._preflight_passed = True
    call_count = 0

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal call_count
        call_count += 1
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # Fail the second setup command (detect by the command content)
        if args and len(args) >= 5 and args[0] == "exec":
            cmd_str = args[4] if len(args) > 4 else ""
            if cmd_str == "failing-command":
                return CommandResult(1, "", "command not found")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-partial",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
                "setup_commands": ["echo hello", "failing-command"],
            },
        )

    assert "provisioning_report" in result
    report = result["provisioning_report"]
    setup_step = next(e for e in report if e["name"] == "setup_commands")
    assert setup_step["status"] == "partial"
    assert "1/2" in setup_step["detail"]
    assert setup_step["error"] is not None


# ---------------------------------------------------------------------------
# Cache clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_clear_requires_no_params(tool: ContainersTool):
    """cache_clear works without purpose (clears all)."""
    # Mock: no cached images found
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "", ""))
    result = await tool.execute("containers", {"operation": "cache_clear"})
    assert result["success"] is True
    assert isinstance(result["cleared"], list)
    assert "detail" in result


@pytest.mark.asyncio
async def test_cache_clear_specific_purpose(tool: ContainersTool):
    """cache_clear with purpose targets a specific image."""
    tool.runtime.run = AsyncMock(return_value=CommandResult(0, "", ""))
    result = await tool.execute("containers", {"operation": "cache_clear", "purpose": "python"})
    assert result["success"] is True
    assert result["cleared"] == ["python"]


# ---------------------------------------------------------------------------
# Cache used in create result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_result_includes_cache_used(tool: ContainersTool):
    """create result includes cache_used field."""
    tool._preflight_passed = True

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # Return failure for image inspect (no cache)
        if args and args[0] == "image":
            return CommandResult(1, "", "No such image")
        # commit call succeeds
        if args and args[0] == "commit":
            return CommandResult(0, "sha256:abc\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-cache-field",
                "purpose": "python",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
            },
        )

    assert "cache_used" in result
    assert result["cache_used"] is False  # No cache existed


@pytest.mark.asyncio
async def test_create_uses_cached_image(tool: ContainersTool):
    """create with a cached image sets cache_used=True and skips profile setup."""
    tool._preflight_passed = True
    executed_commands: list[str] = []

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        # Track exec commands to verify profile setup is skipped
        if args and args[0] == "exec" and len(args) > 4:
            executed_commands.append(args[4])
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # Cache hit: image inspect returns matching hash
        if args and args[0] == "image":
            from amplifier_module_tool_containers.images import get_profile_hash

            expected = get_profile_hash("python")
            return CommandResult(0, f"{expected}\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-cache-hit",
                "purpose": "python",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
                "setup_commands": ["echo user-cmd"],
            },
        )

    assert result["cache_used"] is True
    assert result["image"] == "amplifier-cache:python"
    # Profile setup commands (apt-get, uv) should NOT have been executed
    assert not any("apt-get" in c for c in executed_commands)
    # User's explicit command should still have been executed
    assert "echo user-cmd" in executed_commands


# ---------------------------------------------------------------------------
# Try-repo auto-detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_repo_requires_url(tool: ContainersTool):
    """try-repo purpose without repo_url returns error."""
    tool._preflight_passed = True
    result = await tool.execute(
        "containers",
        {"operation": "create", "purpose": "try-repo"},
    )
    assert "error" in result
    assert "repo_url" in result["error"]


@pytest.mark.asyncio
async def test_try_repo_adds_clone_to_setup(tool: ContainersTool):
    """try-repo adds git clone to setup_commands and resolves detected purpose."""
    tool._preflight_passed = True
    executed_commands: list[str] = []

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "exec" and len(args) > 4:
            executed_commands.append(args[4])
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # No cache
        if args and args[0] == "image":
            return CommandResult(1, "", "No such image")
        # commit succeeds
        if args and args[0] == "commit":
            return CommandResult(0, "sha256:abc\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
        patch(
            "amplifier_module_tool_containers.images.detect_repo_purpose",
            return_value=(
                "python",
                [
                    'uv pip install -e ".[dev]" 2>/dev/null || pip install -e ".[dev]" 2>/dev/null || true'
                ],
            ),
        ),
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-tryrepo",
                "purpose": "try-repo",
                "repo_url": "https://github.com/example/repo.git",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
            },
        )

    assert result.get("success") is True
    # Purpose should have been resolved to python (not try-repo)
    assert result["purpose"] == "python"
    # First setup command should be the git clone
    assert any("git clone" in c for c in executed_commands)
    assert any("https://github.com/example/repo.git" in c for c in executed_commands)


@pytest.mark.asyncio
async def test_try_repo_schema_includes_repo_url(tool: ContainersTool):
    """repo_url is present in the tool input schema."""
    defs = tool.tool_definitions
    schema = defs[0]["input_schema"]
    assert "repo_url" in schema["properties"]
    assert schema["properties"]["repo_url"]["type"] == "string"


# ---------------------------------------------------------------------------
# Background execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exec_background_returns_job_id(tool: ContainersTool, mock_successful_run):
    """exec_background returns a job_id and pid."""
    tool.runtime.run = mock_successful_run
    tool._preflight_passed = True

    # Mock run to return a PID
    async def _bg_run(*args, **kwargs):
        return CommandResult(returncode=0, stdout="12345\n", stderr="")

    tool.runtime.run = _bg_run

    result = await tool.execute(
        "containers",
        {
            "operation": "exec_background",
            "container": "test-container",
            "command": "sleep 10",
        },
    )
    assert "job_id" in result
    assert result["pid"] == "12345"
    assert result["container"] == "test-container"


@pytest.mark.asyncio
async def test_exec_background_requires_container_and_command(tool: ContainersTool):
    """exec_background returns error if container or command missing."""
    result = await tool.execute(
        "containers",
        {
            "operation": "exec_background",
            "container": "test",
        },
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_exec_poll_running(tool: ContainersTool):
    """exec_poll reports running=True when process is active."""
    call_count = 0

    async def _mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # cat exit file (not found yet)
            return CommandResult(returncode=1, stdout="", stderr="")
        elif call_count == 2:  # kill -0 check
            return CommandResult(returncode=0, stdout="running\n", stderr="")
        else:  # tail output
            return CommandResult(returncode=0, stdout="partial output\n", stderr="")

    tool.runtime.run = _mock_run

    result = await tool.execute(
        "containers",
        {
            "operation": "exec_poll",
            "container": "test-container",
            "job_id": "abc12345",
        },
    )
    assert result["running"] is True
    assert "partial output" in result["output"]
    assert result["exit_code"] is None


@pytest.mark.asyncio
async def test_exec_poll_completed(tool: ContainersTool):
    """exec_poll reports running=False with exit_code when done."""
    call_count = 0

    async def _mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # cat exit file (job completed, has exit code)
            return CommandResult(returncode=0, stdout="0\n", stderr="")
        else:  # tail output
            return CommandResult(returncode=0, stdout="all done\n", stderr="")

    tool.runtime.run = _mock_run

    result = await tool.execute(
        "containers",
        {
            "operation": "exec_poll",
            "container": "test-container",
            "job_id": "abc12345",
        },
    )
    assert result["running"] is False
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_exec_poll_requires_container_and_job_id(tool: ContainersTool):
    """exec_poll returns error if container or job_id missing."""
    result = await tool.execute(
        "containers",
        {
            "operation": "exec_poll",
            "container": "test",
        },
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_exec_cancel_returns_cancelled(tool: ContainersTool, mock_successful_run):
    """exec_cancel returns cancelled=True."""
    tool.runtime.run = mock_successful_run
    result = await tool.execute(
        "containers",
        {
            "operation": "exec_cancel",
            "container": "test-container",
            "job_id": "abc12345",
        },
    )
    assert result["cancelled"] is True
    assert result["job_id"] == "abc12345"


# ---------------------------------------------------------------------------
# Two-phase user model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_no_user_flag_on_run(tool: ContainersTool):
    """docker run args do NOT contain --user (container runs as root)."""
    tool._preflight_passed = True
    captured_args: list[tuple[str, ...]] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.append(args)
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-no-user-run",
                "forward_git": False,
                "forward_gh": False,
            },
        )

    run_call = next(c for c in captured_args if c and c[0] == "run")
    assert "--user" not in run_call


@pytest.mark.asyncio
async def test_create_stores_exec_user_in_metadata(tool: ContainersTool):
    """create stores exec_user in metadata."""
    import os

    tool._preflight_passed = True

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-meta",
                "mount_cwd": True,
                "forward_git": False,
                "forward_gh": False,
            },
        )

    metadata = tool.store.load("test-meta")
    assert metadata is not None
    assert "exec_user" in metadata
    assert metadata["exec_user"] == f"{os.getuid()}:{os.getgid()}"


@pytest.mark.asyncio
async def test_create_creates_hostuser(tool: ContainersTool):
    """useradd command is called during container setup."""
    tool._preflight_passed = True
    exec_commands: list[str] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "exec" and len(args) > 4:
            exec_commands.append(args[4])
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-hostuser",
                "forward_git": False,
                "forward_gh": False,
            },
        )

    assert any("useradd" in cmd for cmd in exec_commands)
    assert any("groupadd" in cmd for cmd in exec_commands)


@pytest.mark.asyncio
async def test_create_chowns_workspace(tool: ContainersTool):
    """chown command runs after setup to fix workspace ownership."""
    tool._preflight_passed = True
    exec_commands: list[str] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "exec" and len(args) > 4:
            exec_commands.append(args[4])
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-chown",
                "forward_git": False,
                "forward_gh": False,
            },
        )

    assert any("chown" in cmd and "/workspace" in cmd for cmd in exec_commands)


@pytest.mark.asyncio
async def test_exec_uses_exec_user(tool: ContainersTool):
    """docker exec includes --user from metadata."""
    # Pre-save metadata with exec_user
    tool.store.save("test-exec", {"exec_user": "1000:1000"})

    captured_args: list[tuple[str, ...]] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.append(args)
        return CommandResult(0, "output\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        "containers",
        {
            "operation": "exec",
            "container": "test-exec",
            "command": "ls -la",
        },
    )

    exec_call = captured_args[0]
    assert "--user" in exec_call
    user_idx = exec_call.index("--user")
    assert exec_call[user_idx + 1] == "1000:1000"


@pytest.mark.asyncio
async def test_exec_as_root_skips_user(tool: ContainersTool):
    """as_root=True runs without --user."""
    tool.store.save("test-exec-root", {"exec_user": "1000:1000"})

    captured_args: list[tuple[str, ...]] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.append(args)
        return CommandResult(0, "output\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        "containers",
        {
            "operation": "exec",
            "container": "test-exec-root",
            "command": "apt-get update",
            "as_root": True,
        },
    )

    exec_call = captured_args[0]
    assert "--user" not in exec_call


@pytest.mark.asyncio
async def test_exec_no_mounts_no_user(tool: ContainersTool):
    """mount_cwd=False with no mounts means no exec_user, so exec has no --user."""
    # Metadata without exec_user (simulates container created without mounts)
    tool.store.save("test-nomount", {"exec_user": None})

    captured_args: list[tuple[str, ...]] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.append(args)
        return CommandResult(0, "output\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        "containers",
        {
            "operation": "exec",
            "container": "test-nomount",
            "command": "whoami",
        },
    )

    exec_call = captured_args[0]
    assert "--user" not in exec_call


@pytest.mark.asyncio
async def test_exec_interactive_hint_includes_user(tool: ContainersTool):
    """Interactive hint includes --user when exec_user is set."""
    tool.store.save("test-hint", {"exec_user": "1000:1000"})

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        # test -x /bin/bash succeeds
        return CommandResult(0, "", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]

    result = await tool.execute(
        "containers",
        {
            "operation": "exec_interactive_hint",
            "container": "test-hint",
        },
    )

    assert "--user 1000:1000" in result["command"]


@pytest.mark.asyncio
async def test_exec_background_uses_exec_user(tool: ContainersTool):
    """Background exec respects mapped user."""
    tool.store.save("test-bg", {"exec_user": "1000:1000"})

    captured_args: list[tuple[str, ...]] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.append(args)
        return CommandResult(0, "12345\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]

    await tool.execute(
        "containers",
        {
            "operation": "exec_background",
            "container": "test-bg",
            "command": "long-running-cmd",
        },
    )

    exec_call = captured_args[0]
    assert "--user" in exec_call
    user_idx = exec_call.index("--user")
    assert exec_call[user_idx + 1] == "1000:1000"


@pytest.mark.asyncio
async def test_create_no_cap_drop_all(tool: ContainersTool):
    """docker run args do NOT contain --cap-drop=ALL."""
    tool._preflight_passed = True
    captured_args: list[tuple[str, ...]] = []

    async def _capture(*args: str, **kwargs: object) -> CommandResult:
        captured_args.append(args)
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _capture  # type: ignore[assignment]
    tool.provisioner.runtime.run = _capture  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-no-cap",
                "forward_git": False,
                "forward_gh": False,
            },
        )

    run_call = next(c for c in captured_args if c and c[0] == "run")
    assert "--cap-drop=ALL" not in run_call
    # But security-opt should still be present
    assert "--security-opt=no-new-privileges" in run_call


def test_as_root_in_schema(tool: ContainersTool):
    """as_root is in the tool input schema."""
    defs = tool.tool_definitions
    schema = defs[0]["input_schema"]
    assert "as_root" in schema["properties"]
    assert schema["properties"]["as_root"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# Amplifier purpose parameters
# ---------------------------------------------------------------------------


def test_amplifier_version_in_schema(tool: ContainersTool):
    """amplifier_version is in tool_definitions."""
    defs = tool.tool_definitions
    schema = defs[0]["input_schema"]
    assert "amplifier_version" in schema["properties"]
    assert schema["properties"]["amplifier_version"]["type"] == "string"


def test_amplifier_bundle_in_schema(tool: ContainersTool):
    """amplifier_bundle is in tool_definitions."""
    defs = tool.tool_definitions
    schema = defs[0]["input_schema"]
    assert "amplifier_bundle" in schema["properties"]
    assert schema["properties"]["amplifier_bundle"]["type"] == "string"


@pytest.mark.asyncio
async def test_amplifier_version_modifies_install(tool: ContainersTool):
    """amplifier_version param pins the install version."""
    tool._preflight_passed = True
    executed_commands: list[str] = []

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "exec" and len(args) > 4:
            executed_commands.append(args[4])
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        # No cache
        if args and args[0] == "image":
            return CommandResult(1, "", "No such image")
        # commit succeeds
        if args and args[0] == "commit":
            return CommandResult(0, "sha256:abc\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-amp-ver",
                "purpose": "amplifier",
                "amplifier_version": "1.0.0",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
            },
        )

    assert result.get("success") is True
    # Verify the versioned install command was executed
    assert any("amplifier==1.0.0" in cmd for cmd in executed_commands)
    # Verify the unversioned command was NOT executed
    assert not any(
        "uv tool install amplifier" in cmd and "amplifier==" not in cmd for cmd in executed_commands
    )


@pytest.mark.asyncio
async def test_amplifier_bundle_adds_config_command(tool: ContainersTool):
    """amplifier_bundle param adds bundle configuration command."""
    tool._preflight_passed = True
    executed_commands: list[str] = []

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "exec" and len(args) > 4:
            executed_commands.append(args[4])
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        if args and args[0] == "image":
            return CommandResult(1, "", "No such image")
        if args and args[0] == "commit":
            return CommandResult(0, "sha256:abc\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-amp-bundle",
                "purpose": "amplifier",
                "amplifier_bundle": "github:myorg/mybundle",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
            },
        )

    assert result.get("success") is True
    # Verify the bundle add command was executed
    assert any("amplifier bundle add github:myorg/mybundle" in cmd for cmd in executed_commands)


@pytest.mark.asyncio
async def test_amplifier_settings_provisioned(tool: ContainersTool):
    """amplifier purpose triggers provision_amplifier_settings."""
    tool._preflight_passed = True

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if args and args[0] == "run":
            return CommandResult(0, "abc123def456\n", "")
        if args and args[0] == "image":
            return CommandResult(1, "", "No such image")
        if args and args[0] == "commit":
            return CommandResult(0, "sha256:abc\n", "")
        return CommandResult(0, "/root\n", "")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.provisioner.runtime.run = _mock_run  # type: ignore[assignment]

    with (
        patch("amplifier_module_tool_containers.provisioner.shutil.which", return_value=None),
        patch("amplifier_module_tool_containers.provisioner.Path") as mock_path,
    ):
        mock_home = mock_path.home.return_value
        no_file = type(
            "FP", (), {"exists": lambda self: False, "__str__": lambda self: "/fake/.gitconfig"}
        )()
        mock_home.__truediv__ = lambda self, key: no_file

        result = await tool.execute(
            "containers",
            {
                "operation": "create",
                "name": "test-amp-settings",
                "purpose": "amplifier",
                "forward_git": False,
                "forward_gh": False,
                "forward_ssh": False,
                "dotfiles_skip": True,
            },
        )

    assert "provisioning_report" in result
    step_names = [e["name"] for e in result["provisioning_report"]]
    assert "amplifier_settings" in step_names


# ---------------------------------------------------------------------------
# GPU preflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpu_preflight_nvidia_available(tool: ContainersTool):
    """GPU check reports nvidia available when runtime has it."""

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if "info" in args and "--format" in args:
            return CommandResult(
                returncode=0,
                stdout="map[io.containerd.runc.v2:{} nvidia:{}]",
                stderr="",
            )
        return CommandResult(returncode=0, stdout="", stderr="")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.runtime._runtime = "docker"

    result = await tool.execute("containers", {"operation": "preflight"})
    gpu_check = next(c for c in result["checks"] if c["name"] == "gpu_runtime")
    assert gpu_check["passed"] is True
    assert "NVIDIA runtime available" in gpu_check["detail"]


@pytest.mark.asyncio
async def test_gpu_preflight_nvidia_unavailable(tool: ContainersTool):
    """GPU check reports unavailable but ready is still True."""

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if "info" in args and "--format" in args:
            return CommandResult(
                returncode=0,
                stdout="map[io.containerd.runc.v2:{}]",
                stderr="",
            )
        return CommandResult(returncode=0, stdout="", stderr="")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.runtime._runtime = "docker"

    result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is True  # CRITICAL: GPU absence doesn't break preflight
    gpu_check = next(c for c in result["checks"] if c["name"] == "gpu_runtime")
    assert gpu_check["passed"] is True
    assert "not detected" in gpu_check["detail"]
    assert gpu_check["guidance"] is not None


@pytest.mark.asyncio
async def test_gpu_preflight_podman_skipped(tool: ContainersTool):
    """GPU check on Podman reports not supported."""

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        return CommandResult(returncode=0, stdout="", stderr="")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.runtime._runtime = "podman"

    result = await tool.execute("containers", {"operation": "preflight"})
    gpu_check = next(c for c in result["checks"] if c["name"] == "gpu_runtime")
    assert gpu_check["passed"] is True
    assert "not supported for Podman" in gpu_check["detail"]


@pytest.mark.asyncio
async def test_gpu_check_does_not_affect_ready(tool: ContainersTool):
    """ready=True even when GPU is unavailable — GPU is informational only."""

    async def _mock_run(*args: str, **kwargs: object) -> CommandResult:
        if "info" in args and "--format" in args:
            return CommandResult(
                returncode=0,
                stdout="map[io.containerd.runc.v2:{}]",
                stderr="",
            )
        return CommandResult(returncode=0, stdout="", stderr="")

    tool.runtime.run = _mock_run  # type: ignore[assignment]
    tool.runtime._runtime = "docker"

    result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is True


def test_gpu_flag_in_create_schema():
    """Regression guard: gpu parameter exists in tool schema."""
    t = ContainersTool()
    schema = t.tool_definitions[0]["input_schema"]
    assert "gpu" in schema["properties"]
    assert schema["properties"]["gpu"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# wait_healthy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_healthy_succeeds_first_attempt(tool: ContainersTool):
    """wait_healthy returns healthy=True when check passes immediately."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=0, stdout="ready", stderr="")

    tool.runtime.run = _mock_run
    result = await tool.execute(
        "containers",
        {
            "operation": "wait_healthy",
            "container": "test-db",
            "health_command": "pg_isready",
        },
    )
    assert result["healthy"] is True
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_wait_healthy_succeeds_after_retries(tool: ContainersTool):
    """wait_healthy returns healthy=True after some failed attempts."""
    call_count = 0

    async def _mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return CommandResult(returncode=1, stdout="", stderr="not ready")
        return CommandResult(returncode=0, stdout="ready", stderr="")

    tool.runtime.run = _mock_run
    result = await tool.execute(
        "containers",
        {
            "operation": "wait_healthy",
            "container": "test-db",
            "health_command": "pg_isready",
            "interval": 0,
        },
    )
    assert result["healthy"] is True
    assert result["attempts"] == 3


@pytest.mark.asyncio
async def test_wait_healthy_exhausts_retries(tool: ContainersTool):
    """wait_healthy returns healthy=False when all retries fail."""

    async def _mock_run(*args, **kwargs):
        return CommandResult(returncode=1, stdout="", stderr="connection refused")

    tool.runtime.run = _mock_run
    result = await tool.execute(
        "containers",
        {
            "operation": "wait_healthy",
            "container": "test-db",
            "health_command": "pg_isready",
            "interval": 0,
            "retries": 3,
        },
    )
    assert result["healthy"] is False
    assert result["attempts"] == 3
    assert "connection refused" in result["last_error"]


@pytest.mark.asyncio
async def test_wait_healthy_requires_params(tool: ContainersTool):
    """wait_healthy returns error if container or health_command missing."""
    result = await tool.execute(
        "containers",
        {
            "operation": "wait_healthy",
            "container": "test-db",
        },
    )
    assert "error" in result


def test_wait_healthy_in_schema():
    """wait_healthy is in the operation enum."""
    t = ContainersTool()
    schema = t.tool_definitions[0]["input_schema"]
    ops = schema["properties"]["operation"]["enum"]
    assert "wait_healthy" in ops
