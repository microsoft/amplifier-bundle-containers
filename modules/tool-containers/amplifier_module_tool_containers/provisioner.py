"""Environment variable matching, passthrough resolution, and container provisioning."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import ContainerRuntime


@dataclass
class ProvisioningStep:
    """Result of a single provisioning step."""

    name: str
    status: str  # "success", "skipped", "failed", "partial"
    detail: str
    error: str | None = None


NEVER_PASSTHROUGH = {
    "PATH",
    "HOME",
    "SHELL",
    "USER",
    "LOGNAME",
    "PWD",
    "OLDPWD",
    "TERM",
    "DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "SSH_AUTH_SOCK",
    "SSH_CONNECTION",
    "SSH_CLIENT",
    "SSH_TTY",
    "LS_COLORS",
    "LANG",
    "LC_ALL",
    "HOSTNAME",
    "SHLVL",
    "_",
}

DEFAULT_ENV_PATTERNS = [
    "*_API_KEY",
    "*_TOKEN",
    "*_SECRET",
    "ANTHROPIC_*",
    "OPENAI_*",
    "AZURE_OPENAI_*",
    "GOOGLE_*",
    "GEMINI_*",
    "OLLAMA_*",
    "VLLM_*",
    "AMPLIFIER_*",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
]


def match_env_patterns(env: dict[str, str], patterns: list[str]) -> dict[str, str]:
    """Return env vars whose keys match any of the glob patterns."""
    matched: dict[str, str] = {}
    for key, value in env.items():
        if key in NEVER_PASSTHROUGH:
            continue
        for pattern in patterns:
            if fnmatch.fnmatch(key, pattern):
                matched[key] = value
                break
    return matched


def resolve_env_passthrough(
    mode: str | list[str],
    extra_env: dict[str, str],
    config_patterns: list[str] | None = None,
) -> dict[str, str]:
    """Determine the full set of env vars to inject into a container."""
    host_env = dict(os.environ)
    patterns = config_patterns or DEFAULT_ENV_PATTERNS

    if isinstance(mode, list):
        # Explicit list of var names
        base = {k: host_env[k] for k in mode if k in host_env}
    elif mode == "all":
        base = {k: v for k, v in host_env.items() if k not in NEVER_PASSTHROUGH}
    elif mode == "none":
        base = {}
    else:  # "auto"
        base = match_env_patterns(host_env, patterns)

    # Explicit extra_env always wins
    base.update(extra_env)
    return base


# ---------------------------------------------------------------------------
# Container Provisioner
# ---------------------------------------------------------------------------


class ContainerProvisioner:
    """Handles identity and environment provisioning into containers."""

    def __init__(self, runtime: ContainerRuntime) -> None:
        self.runtime = runtime

    async def get_container_home(self, container: str) -> str:
        """Get the home directory of the container user."""
        result = await self.runtime.run("exec", container, "/bin/sh", "-c", "echo $HOME", timeout=5)
        home = result.stdout.strip()
        return home if home else "/root"

    async def provision_git(self, container: str) -> ProvisioningStep:
        """Copy git configuration into the container."""
        host_home = Path.home()
        if not (host_home / ".gitconfig").exists():
            return ProvisioningStep("forward_git", "skipped", "No .gitconfig found on host")

        home = await self.get_container_home(container)
        copied: list[str] = []
        for src_name, dst_name in [
            (".gitconfig", ".gitconfig"),
            (".gitconfig.local", ".gitconfig.local"),
            (".ssh/known_hosts", ".ssh/known_hosts"),
        ]:
            src = host_home / src_name
            if src.exists():
                dst_path = f"{home}/{dst_name}"
                # Ensure target directory exists
                dst_dir = str(Path(dst_path).parent)
                await self.runtime.run("exec", container, "mkdir", "-p", dst_dir, timeout=5)
                result = await self.runtime.run(
                    "cp", str(src), f"{container}:{dst_path}", timeout=10
                )
                if result.returncode != 0:
                    return ProvisioningStep(
                        "forward_git",
                        "failed",
                        "Failed to copy git config",
                        error=result.stderr.strip(),
                    )
                copied.append(src_name)

        return ProvisioningStep("forward_git", "success", f"Copied {' + '.join(copied)}")

    async def provision_gh_auth(self, container: str) -> ProvisioningStep:
        """Forward GitHub CLI authentication into the container."""
        gh_path = shutil.which("gh")
        if not gh_path:
            return ProvisioningStep("forward_gh", "skipped", "gh CLI not found on host")

        # Extract token from host gh cli
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "auth",
            "token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return ProvisioningStep(
                "forward_gh", "skipped", "gh CLI not authenticated — run 'gh auth login' on host"
            )
        token = stdout.decode().strip()
        if not token:
            return ProvisioningStep(
                "forward_gh", "skipped", "gh CLI not authenticated — run 'gh auth login' on host"
            )

        home = await self.get_container_home(container)
        # Inject as env vars
        bashrc = f"{home}/.bashrc"
        env_script = (
            f'echo "export GH_TOKEN={token}" >> {bashrc} && '
            f'echo "export GITHUB_TOKEN={token}" >> {bashrc}'
        )
        result = await self.runtime.run("exec", container, "/bin/sh", "-c", env_script, timeout=5)
        if result.returncode != 0:
            return ProvisioningStep(
                "forward_gh", "failed", "Failed to inject GH token", error=result.stderr.strip()
            )

        detail_parts = ["GH token injected"]
        # If gh is in the container, do full auth login
        gh_check = await self.runtime.run("exec", container, "which", "gh", timeout=5)
        if gh_check.returncode == 0:
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f'echo "{token}" | gh auth login --with-token',
                timeout=15,
            )
            detail_parts.append("gh auth login completed")

        return ProvisioningStep("forward_gh", "success", " + ".join(detail_parts))

    async def fix_ssh_permissions(self, container: str) -> ProvisioningStep:
        """Fix SSH key permissions after bind mount.

        Copies keys from the read-only staging mount at /tmp/.host-ssh
        into the container user's home .ssh directory with correct permissions.
        """
        home = await self.get_container_home(container)
        ssh_dir = f"{home}/.ssh"
        cmds = [
            f"mkdir -p {ssh_dir}",
            f"cp -r /tmp/.host-ssh/* {ssh_dir}/ 2>/dev/null || true",
            f"chmod 700 {ssh_dir}",
            f"chmod 600 {ssh_dir}/id_* 2>/dev/null || true",
            f"chmod 644 {ssh_dir}/*.pub 2>/dev/null || true",
            f"chmod 644 {ssh_dir}/known_hosts 2>/dev/null || true",
            f"chmod 644 {ssh_dir}/config 2>/dev/null || true",
        ]
        for cmd in cmds:
            result = await self.runtime.run("exec", container, "/bin/sh", "-c", cmd, timeout=5)
            if result.returncode != 0:
                return ProvisioningStep(
                    "forward_ssh",
                    "failed",
                    "Failed to fix SSH permissions",
                    error=result.stderr.strip(),
                )

        return ProvisioningStep("forward_ssh", "success", "SSH keys mounted and permissions fixed")

    async def provision_dotfiles(
        self,
        container: str,
        repo: str,
        script: str | None = None,
        branch: str | None = None,
        target: str = "~/.dotfiles",
    ) -> ProvisioningStep:
        """Clone and apply dotfiles from a git repo."""
        # Clone
        clone_cmd = "git clone --depth=1"
        if branch:
            clone_cmd += f" --branch {branch}"
        clone_cmd += f" {repo} {target}"
        result = await self.runtime.run("exec", container, "/bin/sh", "-c", clone_cmd, timeout=60)
        if result.returncode != 0:
            return ProvisioningStep(
                "dotfiles", "failed", "Failed to clone dotfiles repo", error=result.stderr.strip()
            )

        # Find and run install script
        script_candidates = (
            [script] if script else ["install.sh", "setup.sh", "bootstrap.sh", "script/setup"]
        )
        for candidate in script_candidates:
            check = await self.runtime.run(
                "exec",
                container,
                "test",
                "-f",
                f"{target}/{candidate}",
                timeout=5,
            )
            if check.returncode == 0:
                await self.runtime.run(
                    "exec",
                    container,
                    "/bin/sh",
                    "-c",
                    f"cd {target} && chmod +x {candidate} && ./{candidate}",
                    timeout=300,
                )
                return ProvisioningStep("dotfiles", "success", f"Cloned {repo}, ran {candidate}")

        # Check for Makefile
        make_check = await self.runtime.run(
            "exec",
            container,
            "test",
            "-f",
            f"{target}/Makefile",
            timeout=5,
        )
        if make_check.returncode == 0:
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"cd {target} && make",
                timeout=300,
            )
            return ProvisioningStep("dotfiles", "success", f"Cloned {repo}, ran make")

        # Fallback: smart symlink common dotfiles
        common = [
            ".bashrc",
            ".bash_profile",
            ".bash_aliases",
            ".zshrc",
            ".zprofile",
            ".gitconfig",
            ".gitignore_global",
            ".vimrc",
            ".tmux.conf",
            ".inputrc",
            ".editorconfig",
        ]
        for dotfile in common:
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"test -f {target}/{dotfile} && ln -sf {target}/{dotfile} ~/{dotfile}",
                timeout=5,
            )

        return ProvisioningStep("dotfiles", "success", f"Cloned {repo}, symlinked common dotfiles")

    async def provision_dotfiles_inline(
        self, container: str, files: dict[str, str]
    ) -> ProvisioningStep:
        """Write inline dotfiles content into the container."""
        for path, content in files.items():
            escaped = content.replace("'", "'\\''")
            await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"mkdir -p $(dirname ~/{path}) && cat > ~/{path} << 'AMPLIFIER_DOTFILES_EOF'\n{escaped}\nAMPLIFIER_DOTFILES_EOF",
                timeout=10,
            )

        return ProvisioningStep("dotfiles_inline", "success", f"Wrote {len(files)} dotfiles")
