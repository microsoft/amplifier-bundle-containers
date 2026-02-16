"""Purpose profiles and image resolution for container creation."""

from __future__ import annotations

import asyncio
import hashlib
import shutil as _shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PurposeProfile:
    """Smart defaults for a container purpose."""

    image: str
    packages: list[str] = field(default_factory=list)
    setup_commands: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    forward_git: bool = True
    forward_gh: bool = True
    forward_ssh: bool = False
    dotfiles: bool = True


PURPOSE_PROFILES: dict[str, PurposeProfile] = {
    "python": PurposeProfile(
        image="python:3.12-slim",
        packages=["git", "curl", "build-essential"],
        setup_commands=[
            "pip install --quiet uv",
        ],
    ),
    "node": PurposeProfile(
        image="node:20-slim",
        packages=["git", "curl"],
        setup_commands=["corepack enable"],
    ),
    "rust": PurposeProfile(
        image="rust:1-slim",
        packages=[
            "git",
            "curl",
            "build-essential",
            "pkg-config",
            "libssl-dev",
        ],
    ),
    "go": PurposeProfile(
        image="golang:1.22",
        packages=["git", "curl"],
    ),
    "general": PurposeProfile(
        image="ubuntu:24.04",
        packages=[
            "git",
            "curl",
            "build-essential",
            "wget",
            "jq",
            "tree",
            "vim-tiny",
            "less",
            "make",
        ],
    ),
    "amplifier": PurposeProfile(
        image="python:3.12-slim",
        packages=["git", "curl", "jq"],
        setup_commands=[
            "pip install --quiet uv",
            "UV_TOOL_BIN_DIR=/usr/local/bin uv tool install amplifier",
        ],
        forward_git=True,
        forward_gh=True,
    ),
    "clean": PurposeProfile(
        image="ubuntu:24.04",
        packages=["git", "curl"],
        forward_git=False,
        forward_gh=False,
        forward_ssh=False,
        dotfiles=False,
    ),
}


def get_profile_hash(purpose: str) -> str | None:
    """Get a hash of the purpose profile definition for cache invalidation."""
    profile = PURPOSE_PROFILES.get(purpose)
    if profile is None:
        return None
    return hashlib.md5(str(profile).encode()).hexdigest()[:8]


def resolve_purpose(purpose: str, explicit: dict[str, Any]) -> dict[str, Any]:
    """Merge purpose profile defaults with explicit parameters.

    Explicit parameters always win over purpose defaults.
    """
    profile = PURPOSE_PROFILES.get(purpose)
    if profile is None:
        return explicit

    defaults: dict[str, Any] = {
        "image": profile.image,
        "forward_git": profile.forward_git,
        "forward_gh": profile.forward_gh,
        "forward_ssh": profile.forward_ssh,
    }
    if profile.dotfiles is False:
        defaults["dotfiles_skip"] = True

    # Purpose setup_commands prepend to explicit ones
    purpose_setup: list[str] = []
    if profile.packages:
        pkg_list = " ".join(profile.packages)
        purpose_setup.append(f"apt-get update -qq && apt-get install -y -qq {pkg_list}")
    purpose_setup.extend(profile.setup_commands)

    merged = {**defaults, **{k: v for k, v in explicit.items() if v is not None}}
    existing_setup = merged.get("setup_commands", [])
    # Track which setup commands came from the profile (for cache differentiation)
    merged["_profile_setup_commands"] = purpose_setup
    merged["setup_commands"] = purpose_setup + list(existing_setup)
    if profile.env:
        merged_env = {**profile.env, **merged.get("env", {})}
        merged["env"] = merged_env

    return merged


# ---------------------------------------------------------------------------
# Try-repo auto-detection
# ---------------------------------------------------------------------------

# Detection rules in priority order
REPO_MARKERS: list[tuple[str, str]] = [
    ("Cargo.toml", "rust"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("package.json", "node"),
    ("go.mod", "go"),
]


async def detect_repo_purpose(repo_url: str) -> tuple[str, list[str]]:
    """Shallow clone repo on HOST, inspect files, return (purpose, setup_hints).

    Uses git directly on the host (not container runtime) since this runs
    BEFORE the container is created to determine which purpose profile to use.
    Clone is done via asyncio.create_subprocess_exec into a temp directory
    that is cleaned up after inspection.
    """
    tmpdir = tempfile.mkdtemp(prefix="amp-tryrepo-")
    try:
        # Shallow clone
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth=1",
            "--quiet",
            repo_url,
            tmpdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            return "general", []

        # Detect purpose from file markers
        from pathlib import Path

        repo_path = Path(tmpdir)
        detected_purpose = "general"
        for marker_file, purpose in REPO_MARKERS:
            if (repo_path / marker_file).exists():
                detected_purpose = purpose
                break

        # Generate setup hints based on what we found
        setup_hints: list[str] = []
        if detected_purpose == "python":
            if (repo_path / "pyproject.toml").exists():
                setup_hints.append(
                    'uv pip install -e ".[dev]" 2>/dev/null || pip install -e ".[dev]" 2>/dev/null || true'
                )
            elif (repo_path / "requirements.txt").exists():
                setup_hints.append(
                    "uv pip install -r requirements.txt 2>/dev/null || pip install -r requirements.txt"
                )
        elif detected_purpose == "node":
            setup_hints.append("npm install")
        elif detected_purpose == "rust":
            setup_hints.append("cargo build 2>/dev/null || true")
        elif detected_purpose == "go":
            setup_hints.append("go build ./... 2>/dev/null || true")

        # If Makefile exists, add make as a hint
        if (repo_path / "Makefile").exists():
            setup_hints.append("make 2>/dev/null || true")

        return detected_purpose, setup_hints

    except asyncio.TimeoutError:
        return "general", []
    finally:
        _shutil.rmtree(tmpdir, ignore_errors=True)
