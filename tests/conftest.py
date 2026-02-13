"""Shared fixtures for amplifier-bundle-containers tests."""

from __future__ import annotations

import pytest

from amplifier_module_tool_containers import ContainersTool, MetadataStore
from amplifier_module_tool_containers.runtime import CommandResult, ContainerRuntime


@pytest.fixture
def mock_runtime():
    """ContainerRuntime with docker pre-cached (no real detection)."""
    runtime = ContainerRuntime()
    runtime._runtime = "docker"
    return runtime


@pytest.fixture
def mock_successful_run():
    """An async callable mimicking a successful runtime.run."""

    async def _run(*args, **kwargs):
        return CommandResult(returncode=0, stdout="", stderr="")

    return _run


@pytest.fixture
def tool(tmp_path):
    """ContainersTool with a tmp_path-backed MetadataStore and mocked runtime."""
    t = ContainersTool()
    t.store = MetadataStore(base_dir=tmp_path)
    t.runtime = ContainerRuntime()
    t.runtime._runtime = "docker"
    return t


@pytest.fixture
def metadata_store(tmp_path):
    """MetadataStore rooted in a temporary directory."""
    return MetadataStore(base_dir=tmp_path)
