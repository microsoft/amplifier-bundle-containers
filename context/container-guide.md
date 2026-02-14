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
    user="1000:1000",         # Override exec user (default: host UID:GID)
    repo_url="https://github.com/org/repo",  # For purpose="try-repo"
    cache_bust=false,         # Force fresh build, ignore cached image
    amplifier_version="1.2.3", # Pin amplifier version (amplifier purpose only)
    amplifier_bundle="git+https://...",  # Bundle to configure (amplifier purpose only)
)
```

**Provisioning pipeline** (order matters):
1. Environment variables (env_passthrough + explicit env)
2. Git config forwarding (.gitconfig, .gitconfig.local, known_hosts)
3. GH CLI auth forwarding (extracts token, sets GH_TOKEN)
4. SSH key forwarding (bind-mount ~/.ssh read-only, copied with correct ownership)
5. Amplifier settings forwarding (amplifier purpose only — copies ~/.amplifier/settings.yaml)
6. Dotfiles (clone repo, run install script) — runs AFTER credentials so private repos work
7. Purpose profile setup (language-specific packages and tools)
8. Custom setup_commands

**Security defaults applied to every container**:
- `--security-opt=no-new-privileges` — prevent privilege escalation
- `--memory=4g` (configurable) — prevent host OOM
- `--pids-limit=256` — prevent fork bombs
- Docker's default capability set (allows package installation and user creation)
- Bridge networking (never host by default)
- Never privileged by default

### exec

Run a command inside a container. Runs as the mapped host user by default.

```
containers(operation="exec",
    container="my-env",
    command="cd /workspace && pytest -v",
    timeout=300,
    as_root=false,   # Set true for admin operations (package install, system changes)
)
```

Returns: `{"returncode": 0, "stdout": "...", "stderr": "...", "timed_out": false}`

### exec_background

Start a long-running command in the background. Returns immediately with a job ID.

```
containers(operation="exec_background",
    container="my-env",
    command="amplifier run 'refactor the auth module'",
    as_root=false,
)
```

Returns: `{"job_id": "a3f2e1c8", "pid": "42", "container": "my-env", "command": "..."}`

### exec_poll

Check status and get output of a background job.

```
containers(operation="exec_poll",
    container="my-env",
    job_id="a3f2e1c8",
)
```

Returns:
```json
{"job_id": "a3f2e1c8", "running": true, "output": "... last 100 lines ...", "exit_code": null}
{"job_id": "a3f2e1c8", "running": false, "output": "...", "exit_code": 0}
```

### exec_cancel

Kill a background job.

```
containers(operation="exec_cancel",
    container="my-env",
    job_id="a3f2e1c8",
)
```

Returns: `{"job_id": "a3f2e1c8", "cancelled": true}`

### exec_interactive_hint

Get the exact command for a user to connect interactively.

```
containers(operation="exec_interactive_hint", container="my-env")
```

Returns: `{"command": "docker exec -it --user 1000:1000 my-env /bin/bash", "shell": "/bin/bash"}`

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

### cache_clear

Remove locally cached purpose images. Optionally target a specific purpose.

```
containers(operation="cache_clear")                    # Clear all cached images
containers(operation="cache_clear", purpose="python")  # Clear only python cache
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
- **Settings**: Amplifier settings (~/.amplifier/settings.yaml) forwarded if they exist on host
- **Params**: `amplifier_version` pins a specific version; `amplifier_bundle` adds a bundle via `amplifier bundle add`

### try-repo
- **Image**: Dynamic — inspects the repo to determine language
- **Detection**: Cargo.toml -> rust, pyproject.toml -> python, package.json -> node, go.mod -> go
- **Setup**: Clone repo + detected language setup
- **Requires**: `repo_url` parameter with a git-cloneable URL

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

## Two-Phase User Model

Containers use a two-phase execution model for correct file ownership:

**Setup phase (root)**: The container process runs as root. All provisioning (package installation, user creation, dotfiles) happens with full admin access.

**Exec phase (mapped user)**: A user matching the host UID:GID is created inside the container. All `exec` commands run as this mapped user by default, so files created on mounted volumes have correct host ownership.

**Admin override**: Use `as_root=True` on `exec` or `exec_background` to run as root at any time — useful for post-setup package installation or system configuration changes.

```
docker run ... (root)                           <- Setup phase: apt-get, pip install work
docker exec --user UID:GID container command    <- Exec phase: files owned by host user
docker exec container command                   <- Admin: as_root=True, full root access
```

The `user` parameter on `create` overrides the default UID:GID mapping. Setting `user="root"` disables the mapped user entirely.

---

## Provisioning Report

Every `create` call returns a structured `provisioning_report`. This eliminates the need to investigate what happened — the report tells you:

```json
{
    "success": true,
    "container": "amp-python-a3f2",
    "provisioning_report": [
        {"name": "env_passthrough", "status": "success", "detail": "5 variables injected"},
        {"name": "forward_git", "status": "success", "detail": "Copied .gitconfig, .gitconfig.local"},
        {"name": "forward_gh", "status": "success", "detail": "GH token injected"},
        {"name": "forward_ssh", "status": "skipped", "detail": "Not requested"},
        {"name": "dotfiles", "status": "success", "detail": "Cloned user/dotfiles, ran install.sh"},
        {"name": "setup_commands", "status": "partial", "detail": "2/3 commands succeeded",
         "error": "[{\"command\": \"apt install foo\", \"status\": \"failed\", ...}]"}
    ]
}
```

Each step reports one of: `success`, `skipped`, `failed`, or `partial`.

The response also includes `cache_used: true/false` indicating whether a cached image was used.

---

## Image Caching

Purpose profiles build on stock Docker images but install packages at creation time (slow). Caching accelerates repeated use:

1. After first successful creation with a purpose, the provisioned state is committed as `amplifier-cache:{purpose}` (e.g., `amplifier-cache:python`)
2. On subsequent creates with the same purpose, the cached image is used — skipping package installation
3. Cache is local-only — no registry publishing
4. Cache is version-stamped — automatically invalidated when the profile definition changes

**Managing the cache**:
- `cache_bust=True` on `create` — ignore cache for this one creation, then update the cache
- `cache_clear` operation — remove cached images (one purpose or all)

---

## Try-Repo Auto-Detection

When `purpose="try-repo"` is used with a `repo_url`:

1. The repo is inspected (via `git ls-remote` and file detection) to determine the language
2. Detection rules: `Cargo.toml` -> rust, `pyproject.toml` -> python, `package.json` -> node, `go.mod` -> go
3. The appropriate purpose profile is applied
4. Setup commands are generated: clone the repo, then run language-specific setup hints
5. The container is ready with the repo cloned at `/workspace/repo`

Example:
```
containers(operation="create",
    purpose="try-repo",
    repo_url="https://github.com/org/cool-project",
)
```

---

## Background Execution

For long-running tasks, use the background execution lifecycle:

1. **Start**: `exec_background` returns immediately with a `job_id`
2. **Poll**: `exec_poll` checks if still running, returns last 100 lines of output
3. **Cancel**: `exec_cancel` kills the background process

Background jobs write output to `/tmp/amp-job-{job_id}.out` and exit codes to `/tmp/amp-job-{job_id}.exit` inside the container.

The `as_root` parameter works with `exec_background` the same as with `exec`.

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

Run multiple Amplifier agents in parallel, each in its own container:

```
containers(operation="create", name="agent-a", purpose="amplifier",
    env_passthrough="auto", amplifier_bundle="git+https://github.com/org/bundle@main")
containers(operation="create", name="agent-b", purpose="amplifier",
    env_passthrough="auto")

# Start long-running tasks in the background
containers(operation="exec_background", container="agent-a",
    command="amplifier run 'refactor the auth module'")
containers(operation="exec_background", container="agent-b",
    command="amplifier run 'add test coverage to utils'")

# Poll for results
containers(operation="exec_poll", container="agent-a", job_id="...")
containers(operation="exec_poll", container="agent-b", job_id="...")
```

The amplifier purpose forwards:
- All API keys and credentials (env_passthrough="auto")
- Git config and GH CLI auth
- Amplifier settings (~/.amplifier/settings.yaml) if present on host

Use `amplifier_version` to pin a specific Amplifier version and `amplifier_bundle` to pre-configure a bundle.

---

## Troubleshooting

### Container won't start
1. Run `preflight` — check runtime, daemon, permissions
2. Check `status` with `health_check=true`
3. Check if the image exists: the error from `create` will tell you
4. Verify disk space

### Command fails inside container
1. Check if the container is running: `status`
2. Try a simple command first: `exec` with `echo hello`
3. Check if the tool/binary exists: `exec` with `which <tool>`
4. For package install failures: check network connectivity
5. If permission denied: use `as_root=True` for admin operations

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
3. Verify the target container's service is actually listening: `exec` with `ss -tlnp`
4. Check if the service is bound to 0.0.0.0 (not just 127.0.0.1)

### File ownership issues
1. This is why the two-phase user model exists — exec runs as mapped host user
2. If files are owned by root, check that `exec_user` was set in metadata
3. Use `as_root=True` to fix ownership: `exec` with `chown -R $(id -u):$(id -g) /workspace`
4. The `user` parameter on `create` can override the default UID:GID mapping

### Disk space issues
1. Run `preflight` — it checks disk space
2. Remove unused containers: `destroy_all`
3. Remove cached images: `cache_clear`
4. Remove unused images: suggest user run `docker system prune`

### Stale image cache
1. If a purpose profile seems outdated: `cache_clear(purpose="python")`
2. Or use `cache_bust=True` on `create` for a one-off fresh build
3. Cache auto-invalidates when the profile definition changes
