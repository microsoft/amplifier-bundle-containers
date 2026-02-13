# Container Management — Complete Guide

This is the comprehensive reference for the container-operator agent. It covers all operations, parameters, provisioning details, patterns, and troubleshooting.

## Operations Reference

### preflight

Checks container runtime prerequisites. Returns structured diagnostics.

```
containers(operation="preflight")
```

Returns:
```json
{
    "ready": true,
    "runtime": "docker",
    "checks": [
        {"name": "runtime_installed", "passed": true, "detail": "Found: docker"},
        {"name": "daemon_running", "passed": true, "detail": "Daemon responding"},
        {"name": "user_permissions", "passed": true, "detail": "User can access runtime"},
        {"name": "disk_space", "passed": true, "detail": "42GB free"}
    ]
}
```

If a check fails, `guidance` field provides the fix command. Always run preflight before first create in a session.

### create

Creates a fully provisioned container. This is the primary operation.

```
containers(operation="create",
    name="my-env",           # Optional: auto-generated if omitted
    image="python:3.12-slim", # Optional: purpose profile or config default
    purpose="python",         # Optional: smart defaults (see Purpose Profiles)
    workdir="/workspace",     # Default: /workspace
    mounts=[{"host": "/path", "container": "/path", "mode": "rw"}],
    mount_cwd=true,           # Default: true, mounts CWD to /workspace
    ports=[{"host": 8080, "container": 80}],
    env={"MY_VAR": "value"},
    env_passthrough="auto",   # "auto", "all", "none", or ["VAR1", "VAR2"]
    forward_git=true,
    forward_gh=true,
    forward_ssh=false,
    dotfiles_repo="https://github.com/user/dotfiles",
    dotfiles_script="install.sh",
    dotfiles_branch="main",
    dotfiles_target="~/.dotfiles",
    dotfiles_inline={".bashrc": "alias ll='ls -la'"},
    dotfiles_skip=false,
    setup_commands=["apt update", "pip install -e ."],
    memory_limit="4g",
    cpu_limit=2.0,
    gpu=false,
    network="bridge",         # "bridge", "host", or named network
    persistent=false,
)
```

**Provisioning pipeline** (order matters):
1. Environment variables (env_passthrough + explicit env)
2. Git config forwarding (.gitconfig, .gitconfig.local, known_hosts)
3. GH CLI auth forwarding (extracts token, sets GH_TOKEN)
4. SSH key forwarding (bind-mount ~/.ssh read-only)
5. Dotfiles (clone repo, run install script) — runs AFTER credentials so private repos work
6. Purpose profile setup (language-specific packages and tools)
7. Custom setup_commands

**Security defaults applied to every container**:
- `--cap-drop=ALL` — drop all Linux capabilities
- `--security-opt=no-new-privileges` — prevent privilege escalation
- `--memory=4g` (configurable) — prevent host OOM
- `--pids-limit=256` — prevent fork bombs

### exec

Run a command inside a container.

```
containers(operation="exec",
    container="my-env",
    command="cd /workspace && pytest -v",
    timeout=300,
)
```

Returns: `{"returncode": 0, "stdout": "...", "stderr": "...", "timed_out": false}`

### exec_interactive_hint

Get the exact command for a user to connect interactively.

```
containers(operation="exec_interactive_hint", container="my-env")
```

Returns: `{"command": "docker exec -it my-env /bin/bash", "shell": "/bin/bash"}`

Always provide this to the user when handing off a container.

### list

List all containers managed by this bundle.

```
containers(operation="list")
```

### status

Get detailed container status.

```
containers(operation="status", container="my-env", health_check=true)
```

### destroy / destroy_all

Remove containers.

```
containers(operation="destroy", container="my-env", force=false)
containers(operation="destroy_all", confirm=true)
```

### copy_in / copy_out

Transfer files between host and container.

```
containers(operation="copy_in", container="my-env",
    host_path="/home/user/data.csv", container_path="/workspace/data.csv")
containers(operation="copy_out", container="my-env",
    container_path="/workspace/results/", host_path="/home/user/results/")
```

### snapshot / restore

Save and restore container state.

```
containers(operation="snapshot", container="my-env", name="after-setup")
containers(operation="restore", snapshot="after-setup", name="my-env-v2")
```

### create_network / destroy_network

Manage Docker networks for multi-container communication.

```
containers(operation="create_network", name="my-stack")
containers(operation="destroy_network", name="my-stack")
```

---

## Purpose Profiles

### python
- **Image**: python:3.12-slim
- **Packages**: git, curl, build-essential
- **Setup**: pip install uv, uv venv /workspace/.venv
- **Env**: VIRTUAL_ENV=/workspace/.venv, PATH includes venv bin

### node
- **Image**: node:20-slim
- **Packages**: git, curl
- **Setup**: corepack enable (yarn/pnpm ready)

### rust
- **Image**: rust:1-slim
- **Packages**: git, curl, build-essential, pkg-config, libssl-dev
- **Setup**: cargo ready

### go
- **Image**: golang:1.22
- **Packages**: git, curl
- **Setup**: Go toolchain ready

### general
- **Image**: ubuntu:24.04
- **Packages**: git, curl, build-essential, wget, jq, tree, vim-tiny, less, make
- **Setup**: Common development tools

### amplifier
- **Image**: python:3.12-slim
- **Packages**: git, curl, jq
- **Setup**: pip install uv, uv tool install amplifier
- **Forwarding**: All credentials auto-forwarded (API keys, git, GH auth)
- **Extra**: Amplifier settings forwarded if they exist

### try-repo
- **Image**: Dynamic — inspects the repo to determine language
- **Detection**: Cargo.toml -> rust, pyproject.toml -> python, package.json -> node, go.mod -> go
- **Setup**: Clone repo + detected language setup

### clean
- **Image**: ubuntu:24.04
- **Packages**: Minimal
- **Setup**: None — NO dotfiles, NO credential forwarding
- **Purpose**: Pristine environment for testing "does this work from scratch?"

---

## Environment Variable Passthrough

### "auto" mode (default)
Matches host env vars against configured patterns using glob matching:
- `*_API_KEY` — matches OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.
- `*_TOKEN` — matches GITHUB_TOKEN, etc.
- `ANTHROPIC_*`, `OPENAI_*`, `AZURE_OPENAI_*`, `GOOGLE_*`, `GEMINI_*`, `OLLAMA_*`
- `AMPLIFIER_*` — Amplifier config vars
- `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`

### "all" mode
Passes ALL host env vars except dangerous ones (PATH, HOME, SHELL, etc.).

### "none" mode
Only explicit `env={}` vars. Clean slate.

### Explicit list mode
`env_passthrough=["ANTHROPIC_API_KEY", "MY_CUSTOM_VAR"]` — only named vars.

---

## Dotfiles Integration

### Three-layer model
```
Layer 3: Dotfiles repo (comprehensive, user-maintained)
Layer 2: Individual forwarding (forward_git, forward_gh, etc.)
Layer 1: Explicit env/config (env={...}, setup_commands)
```

Each layer builds on the one below. They are additive, not replacements.

### Dotfiles from a repo
```
containers(operation="create",
    dotfiles_repo="https://github.com/user/dotfiles",
    dotfiles_script="install.sh",  # Optional: auto-detected
    forward_gh=true,               # Needed if dotfiles repo is private
)
```

Install script resolution order:
1. Explicit `dotfiles_script` parameter
2. `install.sh`
3. `setup.sh`
4. `bootstrap.sh`
5. `script/setup`
6. `Makefile` (runs `make`)
7. Smart symlink of common dotfiles (fallback)

### Inline dotfiles
```
containers(operation="create",
    dotfiles_inline={
        ".gitconfig": "[user]\n  name = Test\n  email = test@example.com",
        ".bashrc": "alias ll='ls -la'\nexport EDITOR=vim",
    },
)
```

### Skipping dotfiles
```
containers(operation="create", dotfiles_skip=true)
```
Or use `purpose="clean"` which skips dotfiles automatically.

---

## Multi-Container Patterns

### Service Stack
```
containers(operation="create_network", name="my-stack")

containers(operation="create", name="my-db", image="postgres:16",
    env={"POSTGRES_PASSWORD": "dev"}, network="my-stack")

containers(operation="create", name="my-cache", image="redis:7",
    network="my-stack")

containers(operation="create", name="my-app", purpose="python",
    network="my-stack",
    env={"DATABASE_URL": "postgresql://postgres:dev@my-db:5432/app",
         "REDIS_URL": "redis://my-cache:6379"})
```

Containers on the same named network can reach each other by container name.

### Parallel Isolation
```
containers(operation="create", name="task-1", purpose="python")
containers(operation="create", name="task-2", purpose="python")
containers(operation="create", name="task-3", purpose="python")
```

Each container is independent. No shared network or volumes.

### Amplifier-in-Container
```
containers(operation="create", name="agent-a", purpose="amplifier",
    env_passthrough="auto")
containers(operation="create", name="agent-b", purpose="amplifier",
    env_passthrough="auto")

containers(operation="exec", container="agent-a",
    command="amplifier run 'refactor the auth module'")
containers(operation="exec", container="agent-b",
    command="amplifier run 'add test coverage'")
```

---

## Troubleshooting

### Container won't start
1. Run `preflight` — check runtime, daemon, permissions
2. Check `status` with `health_check=true`
3. Check if the image exists: `exec` with `docker images` on host won't work — check the error from create
4. Verify disk space

### Command fails inside container
1. Check if the container is running: `status`
2. Try a simple command first: `exec` with `echo hello`
3. Check if the tool/binary exists: `exec` with `which <tool>`
4. For package install failures: check network connectivity

### GH auth not working in container
1. Verify `gh auth status` works on the host first
2. Create with `forward_gh=true` explicitly
3. Check inside: `exec` with `echo $GH_TOKEN` — should be non-empty
4. If `gh` CLI is in the container: `exec` with `gh auth status`

### Private dotfiles repo fails to clone
1. Verify `forward_gh=true` is set (needed for HTTPS clones of private repos)
2. Or verify `forward_ssh=true` is set (needed for SSH clones of private repos)
3. GH auth forwarding runs BEFORE dotfiles — if GH auth fails, dotfiles clone will also fail
4. Check the dotfiles repo URL is correct

### Container networking issues
1. Verify both containers are on the same named network
2. Use container NAME (not ID) for addressing
3. Verify the target container's service is actually listening: `exec` with `netstat -tlnp` or `ss -tlnp`
4. Check if the service is bound to 0.0.0.0 (not just 127.0.0.1)

### Disk space issues
1. Run `preflight` — it checks disk space
2. Remove unused containers: `destroy_all`
3. Remove unused images: suggest user run `docker system prune`
4. Remove snapshots: `delete_snapshot`
