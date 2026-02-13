"""Environment variable matching and passthrough resolution."""

from __future__ import annotations

import fnmatch
import os

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
