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

    async def get_container_home(self, container: str, target_home: str | None = None) -> str:
        """Get the home directory for provisioning targets."""
        if target_home:
            return target_home
        result = await self.runtime.run("exec", container, "/bin/sh", "-c", "echo $HOME", timeout=5)
        home = result.stdout.strip()
        return home if home and home != "/" else "/root"

    async def provision_git(
        self, container: str, target_home: str | None = None
    ) -> ProvisioningStep:
        """Copy git configuration into the container."""
        host_home = Path.home()
        if not (host_home / ".gitconfig").exists():
            return ProvisioningStep("forward_git", "skipped", "No .gitconfig found on host")

        home = await self.get_container_home(container, target_home=target_home)
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

        # Inject git identity directly to handle include-based configs.
        # Host gitconfig may use [include] paths that don't exist in the container,
        # so we resolve user.name/user.email on the host and set them explicitly.
        #
        # IMPORTANT: We use pure shell (printf >> file) instead of `git config --file`
        # because git may not be installed in the container yet — setup_commands
        # (which install packages like git) run AFTER provisioning.
        identity_injected: list[str] = []
        resolved: dict[str, str] = {}
        for git_key in ["user.name", "user.email"]:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "config",
                    "--global",
                    git_key,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                git_stdout, _ = await proc.communicate()
                value = git_stdout.decode().strip()
                if value and proc.returncode == 0:
                    resolved[git_key] = value
            except OSError:
                pass  # Skip gracefully if host git config unavailable

        if resolved:
            # Build a [user] block and append via heredoc — no git needed in container.
            # Gitconfig quoting: values with \ or " must be in double-quotes with escaping.
            config_lines = ["[user]"]
            for git_key, value in resolved.items():
                subkey = git_key.split(".", 1)[1]
                if "\\" in value or '"' in value:
                    quoted = value.replace("\\", "\\\\").replace('"', '\\"')
                    config_lines.append(f'\t{subkey} = "{quoted}"')
                else:
                    config_lines.append(f"\t{subkey} = {value}")
            user_block = "\n".join(config_lines)
            gitconfig_path = f"{home}/.gitconfig"
            inject_result = await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"cat >> {gitconfig_path} << 'AMPLIFIER_GIT_IDENTITY_EOF'\n\n{user_block}\nAMPLIFIER_GIT_IDENTITY_EOF",
                timeout=5,
            )
            if inject_result.returncode == 0:
                identity_injected = list(resolved.keys())

        detail = f"Copied {' + '.join(copied)}"
        if identity_injected:
            detail += f", identity set ({' + '.join(identity_injected)})"

        return ProvisioningStep("forward_git", "success", detail)

    async def extract_gh_token(self) -> dict[str, str]:
        """Extract GitHub token from host gh CLI for injection at container creation time.

        Returns a dict of env vars (GH_TOKEN, GITHUB_TOKEN) to pass as -e flags
        to docker run. Returns empty dict if gh is not installed or not authenticated.
        """
        gh_path = shutil.which("gh")
        if not gh_path:
            return {}

        proc = await asyncio.create_subprocess_exec(
            "gh",
            "auth",
            "token",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return {}
        token = stdout.decode().strip()
        if not token:
            return {}

        return {"GH_TOKEN": token, "GITHUB_TOKEN": token}

    async def provision_gh_auth(
        self,
        container: str,
        gh_env_vars: dict[str, str] | None = None,
        target_home: str | None = None,
    ) -> ProvisioningStep:
        """Verify GH token injection and optionally run gh auth login.

        The GH_TOKEN/GITHUB_TOKEN env vars should already be injected at docker run
        time via extract_gh_token(). This method:
        1. Verifies the token is actually visible in the container environment
        2. Runs ``gh auth login --with-token`` if gh CLI is installed in the container
        """
        if not gh_env_vars:
            return ProvisioningStep(
                "forward_gh",
                "skipped",
                "No GH token available (gh CLI missing or not authenticated on host)",
            )

        token = gh_env_vars.get("GH_TOKEN", "")
        if not token:
            return ProvisioningStep(
                "forward_gh",
                "skipped",
                "No GH token available (gh CLI missing or not authenticated on host)",
            )

        detail_parts: list[str] = []

        # Verify token is actually visible in the container environment
        verify = await self.runtime.run(
            "exec", container, "/bin/sh", "-c", "printenv GH_TOKEN", timeout=5
        )
        verify_token = verify.stdout.strip()
        if verify_token == token:
            detail_parts.append("GH_TOKEN verified in container env")
        else:
            return ProvisioningStep(
                "forward_gh",
                "failed",
                "GH_TOKEN not visible in container environment",
                error="Token was passed via -e flag but printenv GH_TOKEN returned empty",
            )

        # If gh CLI is in the container, do full auth login
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

    async def fix_ssh_permissions(
        self, container: str, target_home: str | None = None
    ) -> ProvisioningStep:
        """Fix SSH key permissions after bind mount.

        Copies keys from the read-only staging mount at /tmp/.host-ssh
        into the container user's home .ssh directory with correct permissions.
        """
        home = await self.get_container_home(container, target_home=target_home)
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

    async def provision_amplifier_settings(
        self, container: str, target_home: str | None = None
    ) -> ProvisioningStep:
        """Forward Amplifier settings into the container."""
        home = Path.home()
        amplifier_dir = home / ".amplifier"
        if not amplifier_dir.exists():
            return ProvisioningStep(
                "amplifier_settings", "skipped", "No ~/.amplifier directory on host"
            )

        target = await self.get_container_home(container, target_home=target_home)

        # Create target directory
        await self.runtime.run(
            "exec",
            container,
            "/bin/sh",
            "-c",
            f"mkdir -p {target}/.amplifier",
            timeout=5,
        )

        files_copied = []
        for settings_file in ["settings.yaml", "settings.local.yaml"]:
            src = amplifier_dir / settings_file
            if src.exists():
                await self.runtime.run(
                    "cp",
                    str(src),
                    f"{container}:{target}/.amplifier/{settings_file}",
                    timeout=10,
                )
                files_copied.append(settings_file)

        if not files_copied:
            return ProvisioningStep(
                "amplifier_settings", "skipped", "No settings files found in ~/.amplifier"
            )
        return ProvisioningStep(
            "amplifier_settings", "success", f"Copied {', '.join(files_copied)}"
        )

    async def provision_repos(
        self,
        container: str,
        repos: list[dict[str, str]],
    ) -> ProvisioningStep:
        """Clone repos into the container and optionally run install commands."""
        if not repos:
            return ProvisioningStep("repos", "skipped", "No repos specified")

        cloned: list[str] = []
        failed: list[dict[str, str]] = []
        for repo in repos:
            url = repo.get("url", "")
            path = repo.get("path", f"/workspace/{url.rstrip('/').split('/')[-1]}")
            install = repo.get("install")

            # Clone
            clone_result = await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"git clone {url} {path}",
                timeout=120,
            )
            if clone_result.returncode != 0:
                failed.append({"url": url, "error": clone_result.stderr.strip()})
                continue

            # Install (optional, runs as root since it's a setup operation)
            if install:
                install_result = await self.runtime.run(
                    "exec",
                    container,
                    "/bin/sh",
                    "-c",
                    f"cd {path} && {install}",
                    timeout=300,
                )
                if install_result.returncode != 0:
                    failed.append(
                        {"url": url, "error": f"Install failed: {install_result.stderr.strip()}"}
                    )
                    continue

            cloned.append(url.split("/")[-1])

        if failed and not cloned:
            return ProvisioningStep(
                "repos",
                "failed",
                f"All {len(failed)} repos failed to clone",
                error=str(failed),
            )
        if failed:
            return ProvisioningStep(
                "repos",
                "partial",
                f"{len(cloned)}/{len(cloned) + len(failed)} repos cloned",
                error=str(failed),
            )
        return ProvisioningStep(
            "repos",
            "success",
            f"Cloned {len(cloned)} repos: {', '.join(cloned)}",
        )

    async def provision_config_files(
        self,
        container: str,
        config_files: dict[str, str],
    ) -> ProvisioningStep:
        """Write config files to arbitrary paths inside the container."""
        if not config_files:
            return ProvisioningStep("config_files", "skipped", "No config files specified")

        written: list[str] = []
        failed: list[dict[str, str]] = []
        for path, content in config_files.items():
            escaped = content.replace("'", "'\\''")
            result = await self.runtime.run(
                "exec",
                container,
                "/bin/sh",
                "-c",
                f"mkdir -p $(dirname '{path}') && cat > '{path}' << 'AMPLIFIER_CONFIG_EOF'\n{escaped}\nAMPLIFIER_CONFIG_EOF",
                timeout=10,
            )
            if result.returncode == 0:
                written.append(path)
            else:
                failed.append({"path": path, "error": result.stderr.strip()})

        if failed and not written:
            return ProvisioningStep(
                "config_files",
                "failed",
                f"All {len(failed)} files failed",
                error=str(failed),
            )
        if failed:
            return ProvisioningStep(
                "config_files",
                "partial",
                f"{len(written)}/{len(written) + len(failed)} files written",
                error=str(failed),
            )
        return ProvisioningStep(
            "config_files",
            "success",
            f"Wrote {len(written)} files: {', '.join(written)}",
        )

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
