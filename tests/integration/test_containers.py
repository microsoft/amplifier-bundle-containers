"""Integration tests for amplifier-bundle-containers tool module.

These tests use a REAL Docker/Podman runtime. They create actual containers,
execute commands, copy files, and verify behavior end-to-end.

Requirements:
    - Docker or Podman installed and daemon running
    - alpine:3.19 image available (pulled automatically if missing)
    - pytest-asyncio, pytest-timeout

Run with:
    pytest tests/integration/ -v --timeout=120
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid

import pytest
import pytest_asyncio

# Add module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../modules/tool-containers"))

from amplifier_module_tool_containers import ContainersTool, MetadataStore

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]

# Name prefix to avoid collisions with other containers
NAME_PREFIX = "test-amp-"
TEST_IMAGE = "alpine:3.19"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_name() -> str:
    return f"{NAME_PREFIX}{uuid.uuid4().hex[:8]}"


async def _force_destroy(tool: ContainersTool, name: str) -> None:
    """Best-effort container cleanup."""
    try:
        await tool.execute(
            {
                "operation": "destroy",
                "container": name,
                "force": True,
            },
        )
    except Exception:
        # Last resort: shell out directly
        runtime = await tool.runtime.detect()
        if runtime:
            proc = await asyncio.create_subprocess_exec(
                runtime,
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def check_docker():
    """Skip all tests if no container runtime available."""
    if not (shutil.which("docker") or shutil.which("podman")):
        pytest.skip("No container runtime (docker/podman) available")


@pytest.fixture(scope="session")
def ensure_image(check_docker):
    """Ensure the test image is pulled."""
    import subprocess

    runtime = "podman" if shutil.which("podman") else "docker"
    result = subprocess.run(
        [runtime, "image", "inspect", TEST_IMAGE],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run(
            [runtime, "pull", TEST_IMAGE],
            check=True,
            capture_output=True,
        )


@pytest_asyncio.fixture
async def tool(tmp_path, check_docker, ensure_image):
    """Create a ContainersTool with an isolated metadata store."""
    t = ContainersTool(config={"default_image": TEST_IMAGE})
    t.store = MetadataStore(base_dir=tmp_path / "metadata")
    # Bypass ToolResult wrapping so tests can assert on raw dicts
    t._wrap_result = lambda result: result
    return t


@pytest_asyncio.fixture
async def container(tool):
    """Create a minimal test container and destroy it after the test."""
    name = _unique_name()
    result = await tool.execute(
        {
            "operation": "create",
            "name": name,
            "image": TEST_IMAGE,
            "mount_cwd": False,
            "env_passthrough": "none",
            "forward_git": False,
            "forward_gh": False,
            "forward_ssh": False,
            "dotfiles_skip": True,
            "setup_commands": [],
        },
    )
    assert result.get("success"), f"Container creation failed: {result}"
    yield name
    await _force_destroy(tool, name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPreflight:
    async def test_preflight_passes(self, tool: ContainersTool):
        """Preflight should detect the runtime and report ready."""
        result = await tool.execute({"operation": "preflight"})

        assert result["ready"] is True
        assert result["runtime"] in ("docker", "podman")
        assert result["summary"] == "Container runtime ready"

        # Every check should have passed
        for check in result["checks"]:
            assert check["passed"] is True, f"Check '{check['name']}' failed: {check['detail']}"


class TestContainerLifecycle:
    async def test_create_and_destroy(self, tool: ContainersTool):
        """Create a container, verify it's running, destroy it, verify gone."""
        name = _unique_name()
        try:
            # Create
            create_result = await tool.execute(
                {
                    "operation": "create",
                    "name": name,
                    "image": TEST_IMAGE,
                    "mount_cwd": False,
                    "env_passthrough": "none",
                    "forward_git": False,
                    "forward_gh": False,
                    "forward_ssh": False,
                    "dotfiles_skip": True,
                    "setup_commands": [],
                },
            )
            assert create_result.get("success"), f"Create failed: {create_result}"
            assert create_result["container"] == name
            assert create_result["image"] == TEST_IMAGE

            # Verify it exists via docker ps
            verify = await tool.runtime.run(
                "ps",
                "--filter",
                f"name=^{name}$",
                "--format",
                "{{.Names}}",
                timeout=10,
            )
            assert name in verify.stdout, f"Container {name} not found in docker ps output"

            # Destroy
            destroy_result = await tool.execute(
                {
                    "operation": "destroy",
                    "container": name,
                    "force": True,
                },
            )
            assert destroy_result.get("success"), f"Destroy failed: {destroy_result}"

            # Verify it's gone
            verify_gone = await tool.runtime.run(
                "ps",
                "-a",
                "--filter",
                f"name=^{name}$",
                "--format",
                "{{.Names}}",
                timeout=10,
            )
            assert name not in verify_gone.stdout, f"Container {name} still exists after destroy"
        finally:
            # Safety cleanup
            await _force_destroy(tool, name)


class TestExec:
    async def test_exec_echo(self, tool: ContainersTool, container: str):
        """Execute a simple echo command and verify output."""
        result = await tool.execute(
            {
                "operation": "exec",
                "container": container,
                "command": "echo hello-world",
            },
        )

        assert result["returncode"] == 0
        assert "hello-world" in result["stdout"]
        assert result["timed_out"] is False

    async def test_exec_failure(self, tool: ContainersTool, container: str):
        """Execute a command that fails and verify non-zero return."""
        result = await tool.execute(
            {
                "operation": "exec",
                "container": container,
                "command": "false",
            },
        )

        assert result["returncode"] != 0

    async def test_exec_interactive_hint(self, tool: ContainersTool, container: str):
        """Get an interactive hint and verify it contains the name and shell."""
        result = await tool.execute(
            {
                "operation": "exec_interactive_hint",
                "container": container,
            },
        )

        assert container in result["command"]
        # Alpine has /bin/sh (and possibly /bin/bash if busybox aliases it)
        assert result["shell"] in ("/bin/sh", "/bin/bash", "/bin/zsh")
        assert container in result["container"]


class TestEnvPassthrough:
    async def test_env_passthrough_auto(self, tool: ContainersTool):
        """With env_passthrough='auto', matching env vars are forwarded."""
        name = _unique_name()
        env_key = "TEST_AMPLIFIER_API_KEY"
        env_val = f"test-secret-{uuid.uuid4().hex[:8]}"
        original = os.environ.get(env_key)

        try:
            os.environ[env_key] = env_val

            create_result = await tool.execute(
                {
                    "operation": "create",
                    "name": name,
                    "image": TEST_IMAGE,
                    "mount_cwd": False,
                    "env_passthrough": "auto",
                    "forward_git": False,
                    "forward_gh": False,
                    "forward_ssh": False,
                    "dotfiles_skip": True,
                    "setup_commands": [],
                },
            )
            assert create_result.get("success"), f"Create failed: {create_result}"

            exec_result = await tool.execute(
                {
                    "operation": "exec",
                    "container": name,
                    "command": f"printenv {env_key}",
                },
            )
            assert exec_result["returncode"] == 0
            assert env_val in exec_result["stdout"]
        finally:
            # Restore env
            if original is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = original
            await _force_destroy(tool, name)

    async def test_env_passthrough_none(self, tool: ContainersTool):
        """With env_passthrough='none', no host env vars are forwarded."""
        name = _unique_name()
        env_key = "TEST_AMPLIFIER_API_KEY"
        env_val = f"should-not-appear-{uuid.uuid4().hex[:8]}"
        original = os.environ.get(env_key)

        try:
            os.environ[env_key] = env_val

            create_result = await tool.execute(
                {
                    "operation": "create",
                    "name": name,
                    "image": TEST_IMAGE,
                    "mount_cwd": False,
                    "env_passthrough": "none",
                    "forward_git": False,
                    "forward_gh": False,
                    "forward_ssh": False,
                    "dotfiles_skip": True,
                    "setup_commands": [],
                },
            )
            assert create_result.get("success"), f"Create failed: {create_result}"

            exec_result = await tool.execute(
                {
                    "operation": "exec",
                    "container": name,
                    "command": "printenv",
                },
            )
            assert env_val not in exec_result["stdout"], (
                f"Env var {env_key} should NOT be present with mode='none'"
            )
        finally:
            if original is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = original
            await _force_destroy(tool, name)


class TestMounts:
    async def test_cwd_mount(self, tool: ContainersTool):
        """With mount_cwd=True, the host CWD is mounted at /workspace."""
        name = _unique_name()
        try:
            create_result = await tool.execute(
                {
                    "operation": "create",
                    "name": name,
                    "image": TEST_IMAGE,
                    "mount_cwd": True,
                    "env_passthrough": "none",
                    "forward_git": False,
                    "forward_gh": False,
                    "forward_ssh": False,
                    "dotfiles_skip": True,
                    "setup_commands": [],
                },
            )
            assert create_result.get("success"), f"Create failed: {create_result}"

            # The /workspace directory should exist and contain host files
            exec_result = await tool.execute(
                {
                    "operation": "exec",
                    "container": name,
                    "command": "ls /workspace",
                },
            )
            assert exec_result["returncode"] == 0
            # The directory should at minimum exist (ls returns 0)
            # If we're running from the bundle root, we should see bundle files
            # Just verify the mount point works
            assert exec_result["timed_out"] is False
        finally:
            await _force_destroy(tool, name)


class TestList:
    async def test_list_shows_container(self, tool: ContainersTool, container: str):
        """A created container should appear in the list operation."""
        result = await tool.execute({"operation": "list"})

        assert result["count"] >= 1
        names = [c["name"] for c in result["containers"]]
        assert container in names, f"Container {container} not in list: {names}"


class TestMetadata:
    async def test_metadata_persists(self, tool: ContainersTool, tmp_path):
        """Metadata is saved on create and cleaned up on destroy."""
        name = _unique_name()
        # Use the tool's already-isolated metadata store
        meta_dir = tool.store.containers_dir / name

        try:
            create_result = await tool.execute(
                {
                    "operation": "create",
                    "name": name,
                    "image": TEST_IMAGE,
                    "mount_cwd": False,
                    "env_passthrough": "none",
                    "forward_git": False,
                    "forward_gh": False,
                    "forward_ssh": False,
                    "dotfiles_skip": True,
                    "setup_commands": [],
                },
            )
            assert create_result.get("success"), f"Create failed: {create_result}"

            # Metadata file should exist
            meta_file = meta_dir / "metadata.json"
            assert meta_file.exists(), f"Expected metadata at {meta_file}"

            # Load and verify content
            import json

            metadata = json.loads(meta_file.read_text())
            assert metadata["name"] == name
            assert metadata["image"] == TEST_IMAGE

            # Destroy should clean up metadata
            destroy_result = await tool.execute(
                {
                    "operation": "destroy",
                    "container": name,
                    "force": True,
                },
            )
            assert destroy_result.get("success")
            assert not meta_dir.exists(), f"Metadata dir {meta_dir} should be removed after destroy"
        finally:
            await _force_destroy(tool, name)


class TestCopyInOut:
    async def test_copy_in_and_out(self, tool: ContainersTool, container: str, tmp_path):
        """Copy a file into the container and back out, verify contents."""
        # Write a test file on the host
        test_content = f"integration-test-{uuid.uuid4().hex}"
        src_file = tmp_path / "test_input.txt"
        src_file.write_text(test_content)

        # Copy into container
        container_path = "/tmp/test_input.txt"
        copy_in_result = await tool.execute(
            {
                "operation": "copy_in",
                "container": container,
                "host_path": str(src_file),
                "container_path": container_path,
            },
        )
        assert copy_in_result.get("success"), f"copy_in failed: {copy_in_result}"

        # Verify contents inside container via exec
        cat_result = await tool.execute(
            {
                "operation": "exec",
                "container": container,
                "command": f"cat {container_path}",
            },
        )
        assert cat_result["returncode"] == 0
        assert test_content in cat_result["stdout"]

        # Copy back out to a different host path
        dst_file = tmp_path / "test_output.txt"
        copy_out_result = await tool.execute(
            {
                "operation": "copy_out",
                "container": container,
                "container_path": container_path,
                "host_path": str(dst_file),
            },
        )
        assert copy_out_result.get("success"), f"copy_out failed: {copy_out_result}"

        # Verify the round-tripped content matches
        assert dst_file.read_text() == test_content
