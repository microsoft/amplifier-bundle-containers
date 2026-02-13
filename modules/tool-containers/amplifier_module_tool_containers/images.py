"""Purpose profiles and image resolution for container creation."""

from __future__ import annotations

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
            "uv venv /workspace/.venv",
        ],
        env={
            "VIRTUAL_ENV": "/workspace/.venv",
            "PATH": "/workspace/.venv/bin:$PATH",
        },
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
            "uv tool install amplifier",
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
    merged["setup_commands"] = purpose_setup + list(existing_setup)
    if profile.env:
        merged_env = {**profile.env, **merged.get("env", {})}
        merged["env"] = merged_env

    return merged
