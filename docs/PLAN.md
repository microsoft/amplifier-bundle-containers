# Implementation Plan: amplifier-bundle-containers

> **Design doc**: [DESIGN.md](../DESIGN.md)
> **Status**: Ready for implementation
> **Phases**: 4 (Core MVP -> Convenience -> Multi-Container -> Polish)

---

## Prerequisites

Before starting implementation:
- Read `DESIGN.md` for full architecture context
- Have Docker or Podman installed locally for testing
- Clone `amplifier-bundle-shadow` as reference: `~/repos/amplifier-bundle-shadow`
- Clone `amplifier-bundle-recipes` as structural reference
- Ensure `amplifier-core` and `amplifier-foundation` are available locally

---

## Phase 1: Core MVP

> Goal: Basic container lifecycle works end-to-end. An assistant can create a container, run commands in it, and destroy it, with env var passthrough and git config forwarding.

### Task 1.1: Bundle Skeleton and Packaging

**What**: Create the thin root bundle, core behavior YAML, and tool module package scaffolding.

**Files to create**:
- `bundle.md` — Thin root. Includes foundation + `containers:behaviors/containers`.
- `behaviors/containers.yaml` — Core behavior. Declares `tool-containers` module, agent include, context include.
- `modules/tool-containers/pyproject.toml` — Package `amplifier-module-tool-containers`. Zero runtime deps on amplifier-core (peer dependency). Entry point: `amplifier.modules` group.
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — Stub with `mount()` function that returns `[ContainersTool(config)]`. Stub `ContainersTool` class that implements `ToolHandler` with empty `tool_definitions` and `execute`.
- `LICENSE` — MIT

**Pattern reference**: Follow `amplifier-bundle-shadow/bundles/shadow.yaml` for behavior YAML structure. Follow `amplifier-bundle-shadow/modules/tool-shadow/pyproject.toml` for module packaging.

**`bundle.md` spec**:
```yaml
---
bundle:
  name: containers
  version: 0.1.0
  description: General-purpose container management for Amplifier agents

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: containers:behaviors/containers
---
# Container Management

@containers:context/container-awareness.md
```

**`behaviors/containers.yaml` spec**:
```yaml
bundle:
  name: behavior-containers
  version: 0.1.0
  description: Container management for isolated environments

tools:
  - module: tool-containers
    source: "containers:modules/tool-containers"
    config:
      default_image: "ubuntu:24.04"
      max_containers: 10
      auto_passthrough:
        env_patterns:
          - "*_API_KEY"
          - "*_TOKEN"
          - "ANTHROPIC_*"
          - "OPENAI_*"
          - "AZURE_OPENAI_*"
          - "GOOGLE_*"
          - "GEMINI_*"
          - "OLLAMA_*"
          - "AMPLIFIER_*"
          - "HTTP_PROXY"
          - "HTTPS_PROXY"
          - "NO_PROXY"
        forward_git: true
        forward_gh: true
        forward_ssh: false
      security:
        allow_privileged: false
        allow_host_network: false
        cap_drop: ["ALL"]
        memory_limit: "4g"
        pids_limit: 256

context:
  include:
    - containers:context/container-awareness.md
```

**`modules/tool-containers/pyproject.toml` spec**:
```toml
[project]
name = "amplifier-module-tool-containers"
version = "0.1.0"
description = "Container management tool for Amplifier agents"
requires-python = ">=3.11"
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["amplifier_module_tool_containers"]

[project.entry-points."amplifier.modules"]
tool-containers = "amplifier_module_tool_containers:mount"
```

**Tests**:
- Verify `mount()` returns a list with one `ContainersTool` instance
- Verify `tool_definitions` returns a valid tool spec with `"containers"` name and `operation` enum
- Verify `execute()` with unknown operation returns error dict

**Done when**: `amplifier run --bundle ./bundle.md "list containers"` loads the bundle and the tool appears in the tool list (even though operations are stubs).

---

### Task 1.2: Container Runtime Detection

**What**: Implement `runtime.py` — auto-detect Docker or Podman, provide async command execution, handle errors gracefully.

**File**: `modules/tool-containers/amplifier_module_tool_containers/runtime.py`

**Class: `ContainerRuntime`**

```python
class ContainerRuntime:
    """Detects and wraps Docker or Podman CLI."""

    async def detect(self) -> str | None:
        """Return 'docker' or 'podman' or None. Prefer podman for rootless."""

    async def run(self, *args: str, timeout: int = 300) -> CommandResult:
        """Execute runtime command. Returns CommandResult(returncode, stdout, stderr)."""

    async def is_daemon_running(self) -> bool:
        """Check if the container daemon is responsive."""

    async def user_has_permissions(self) -> bool:
        """Check if current user can run containers without sudo."""

    async def get_info(self) -> dict:
        """Return runtime info (version, storage driver, etc.)."""
```

**Implementation notes**:
- Use `shutil.which()` for detection: check `podman` first (rootless preferred), then `docker`
- All commands via `asyncio.create_subprocess_exec` — never `shell=True`
- `CommandResult` is a simple dataclass: `returncode: int, stdout: str, stderr: str`
- Timeout handling via `asyncio.wait_for` wrapping `proc.communicate()`
- Cache the detected runtime after first call

**Tests**:
- `test_detect_podman_preferred` — when both exist, podman wins
- `test_detect_docker_fallback` — when only docker exists, use it
- `test_detect_none` — when neither exists, returns None
- `test_run_success` — successful command returns stdout
- `test_run_failure` — failed command returns stderr and nonzero returncode
- `test_run_timeout` — command exceeding timeout raises/returns error
- `test_daemon_check` — daemon running vs not running
- Mock `shutil.which` and `asyncio.create_subprocess_exec` for unit tests

**Done when**: All tests pass. `ContainerRuntime` can detect, run commands, and handle errors.

---

### Task 1.3: Preflight Operation

**What**: Implement the `preflight` operation — structured health check of container prerequisites.

**File**: Add `_op_preflight` method to `ContainersTool` in `__init__.py`

**Preflight checks** (in order):

| Check | How | Auto-fixable | Fix guidance |
|-------|-----|-------------|-------------|
| `runtime_installed` | `runtime.detect()` | No | Platform-specific install instructions |
| `daemon_running` | `runtime.is_daemon_running()` | Attempt `systemctl start docker` | Manual start command |
| `user_permissions` | `runtime.user_has_permissions()` | No | `sudo usermod -aG docker $USER` |
| `disk_space` | `shutil.disk_usage('/')` | Partial: `docker system prune` | Free space guidance |

**Return schema**:
```python
{
    "ready": bool,              # All checks passed
    "runtime": str | None,      # "docker", "podman", or None
    "checks": [
        {
            "name": str,
            "passed": bool,
            "detail": str,
            "auto_fixable": bool,
            "fix_applied": bool,     # True if auto-fix was attempted and succeeded
            "guidance": str | None,  # Human-readable fix instructions
        }
    ],
    "summary": str
}
```

**Implementation notes**:
- Auto-fix should only be attempted if `auto_fix=True` parameter is passed (default True)
- Disk space check: warn if <5GB free, fail if <1GB
- Platform detection for guidance: check `sys.platform`, check for WSL via `/proc/version`
- Provide platform-specific install instructions: Debian/Ubuntu (`apt`), Fedora (`dnf`), macOS (`brew cask`/Docker Desktop), Arch (`pacman`), WSL (Docker Desktop)

**Tests**:
- `test_preflight_all_pass` — everything healthy
- `test_preflight_no_runtime` — clear guidance per platform
- `test_preflight_daemon_not_running` — with auto-fix attempt
- `test_preflight_no_permissions` — guidance for usermod
- `test_preflight_low_disk` — warning vs failure thresholds

**Done when**: `containers(operation="preflight")` returns structured diagnostics that the assistant can act on.

---

### Task 1.4: Container Lifecycle — Create and Destroy

**What**: Implement `lifecycle.py` — creating containers with proper flags and destroying them.

**File**: `modules/tool-containers/amplifier_module_tool_containers/lifecycle.py`

**Class: `ContainerLifecycle`**

```python
class ContainerLifecycle:
    """Manages container creation, tracking, and destruction."""

    def __init__(self, runtime: ContainerRuntime, config: dict):
        self.runtime = runtime
        self.config = config
        self.metadata_dir = Path.home() / ".amplifier" / "containers"

    async def create(self, params: CreateParams) -> CreateResult:
        """Create a container with given parameters."""

    async def destroy(self, name_or_id: str, force: bool = False) -> DestroyResult:
        """Stop and remove a container."""

    async def destroy_all(self) -> list[DestroyResult]:
        """Destroy all managed containers."""

    async def list_containers(self) -> list[ContainerInfo]:
        """List all managed containers."""

    async def get_status(self, name_or_id: str, health_check: bool = False) -> ContainerStatus:
        """Get detailed status of a container."""
```

**`CreateParams` dataclass** (maps from tool input):
```python
@dataclass
class CreateParams:
    name: str | None = None          # Auto-generate if not provided
    image: str = "ubuntu:24.04"
    workdir: str = "/workspace"
    mounts: list[dict] = field(default_factory=list)
    mount_cwd: bool = True
    ports: list[dict] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    memory_limit: str = "4g"
    cpu_limit: float | None = None
    network: str = "bridge"
    persistent: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
```

**Container creation flow**:
1. Auto-generate name if not provided: `amp-{purpose}-{random_suffix}` or `amp-{random_suffix}`
2. Build `docker run` argument list:
   - `-d` (detached)
   - `--name {name}`
   - `--hostname {name}`
   - `-w {workdir}`
   - Security flags: `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--memory={limit}`, `--pids-limit={limit}`
   - Mount flags: `-v {host}:{container}:{mode}` for each mount + CWD
   - Port flags: `-p {host}:{container}` for each port mapping
   - Env flags: `-e {KEY}={VALUE}` for each env var
   - Network: `--network={network}`
   - Labels: `-l {key}={value}` for each label + standard amplifier labels
   - Image
   - Command: `sleep infinity` (keeps container running for exec)
3. Run `docker run` via runtime
4. Verify container started: `docker inspect --format '{{.State.Running}}'`
5. Save metadata to `~/.amplifier/containers/containers/{name}/metadata.json`
6. Return `CreateResult` with container name, id, and status

**Standard labels** (always added):
```python
STANDARD_LABELS = {
    "amplifier.managed": "true",
    "amplifier.bundle": "containers",
    "amplifier.created": "<ISO timestamp>",
}
# Plus: amplifier.session, amplifier.purpose, amplifier.persistent
```

**Metadata schema** (`metadata.json`):
```json
{
    "name": "amp-python-a3f2",
    "container_id": "abc123...",
    "image": "python:3.12-slim",
    "purpose": "python",
    "created": "2025-02-12T07:00:00Z",
    "session_id": "fcc60ce7-...",
    "persistent": false,
    "mounts": [...],
    "ports": [...],
    "env_keys": ["ANTHROPIC_API_KEY"],
    "provisioning": {
        "forward_git": true,
        "forward_gh": true,
        "dotfiles_repo": null
    }
}
```

**Destroy flow**:
1. `docker stop {name}` (with timeout, or `docker kill` if force)
2. `docker rm {name}`
3. Remove metadata from `~/.amplifier/containers/containers/{name}/`
4. Return result

**List flow**:
1. `docker ps -a --filter label=amplifier.managed=true --format json`
2. Parse JSON output, merge with local metadata
3. Return list of `ContainerInfo`

**Tests**:
- `test_create_minimal` — create with just defaults, verify docker run args
- `test_create_with_mounts` — verify mount flags
- `test_create_with_ports` — verify port flags
- `test_create_with_env` — verify env flags
- `test_create_name_generation` — auto-generated names are valid container names
- `test_create_security_flags` — cap-drop, no-new-privileges, memory, pids always present
- `test_create_labels` — standard labels always present
- `test_create_metadata_saved` — metadata.json written correctly
- `test_destroy_success` — container stopped and removed, metadata cleaned
- `test_destroy_force` — uses kill instead of stop
- `test_destroy_nonexistent` — returns error, doesn't crash
- `test_list_empty` — no containers returns empty list
- `test_list_with_containers` — returns correct info
- Mock `runtime.run` for all tests

**Done when**: `create` and `destroy` work end-to-end, containers are tracked with labels and metadata.

---

### Task 1.5: Exec Operation

**What**: Implement command execution inside containers.

**Add to `lifecycle.py`**:

```python
async def exec_command(
    self,
    container: str,
    command: str,
    timeout: int = 300,
    workdir: str | None = None,
) -> ExecResult:
    """Execute a command inside a running container."""
```

**Implementation**:
1. Verify container exists and is running
2. Build exec args: `docker exec [-w workdir] {container} /bin/sh -c "{command}"`
3. Run via `runtime.run()` with timeout
4. Return `ExecResult(returncode, stdout, stderr, timed_out)`

**Also implement `exec_interactive_hint`**:
```python
async def exec_interactive_hint(self, container: str) -> dict:
    """Return the shell command for user to connect interactively."""
    runtime = await self.runtime.detect()
    # Detect available shell in container
    for shell in ["/bin/bash", "/bin/zsh", "/bin/sh"]:
        result = await self.exec_command(container, f"test -x {shell}")
        if result.returncode == 0:
            return {
                "command": f"{runtime} exec -it {container} {shell}",
                "shell": shell,
                "container": container,
            }
```

**Tests**:
- `test_exec_success` — runs command, captures stdout
- `test_exec_failure` — nonzero exit code captured
- `test_exec_timeout` — command exceeding timeout returns timed_out=True
- `test_exec_with_workdir` — -w flag added
- `test_exec_on_stopped_container` — returns error
- `test_exec_interactive_hint_bash` — prefers bash
- `test_exec_interactive_hint_sh_fallback` — falls back to sh

**Done when**: Can run arbitrary commands in containers and get structured results.

---

### Task 1.6: Environment Variable Passthrough

**What**: Implement the three-mode env var passthrough system.

**Add to `provisioner.py`**:

```python
class ContainerProvisioner:
    """Handles identity and environment provisioning into containers."""

    async def provision_env_vars(
        self,
        container: str,
        mode: str | list[str],
        extra_env: dict[str, str],
        config_patterns: list[str],
    ) -> ProvisionResult:
        """Provision environment variables into a container."""
```

**Modes**:
- `"auto"` — Match host env vars against `config_patterns` using `fnmatch`
- `"all"` — Pass all host env vars (excluding dangerous ones like `PATH`, `HOME`, `SHELL`, `USER`)
- `"none"` — No passthrough, only explicit `extra_env`
- `list[str]` — Explicit variable names to pass through

**Implementation**:
1. Determine which env vars to pass based on mode
2. Merge with explicit `extra_env` (explicit wins on conflict)
3. Write a file `/tmp/.amplifier_env` inside the container with `KEY=VALUE` lines
4. Append `set -a; source /tmp/.amplifier_env; set +a` to container's `.bashrc`
5. Also set vars immediately via `docker exec` for the current session

**Pattern matching** (`fnmatch` for glob patterns):
```python
import fnmatch

def match_env_patterns(env: dict[str, str], patterns: list[str]) -> dict[str, str]:
    matched = {}
    for key, value in env.items():
        for pattern in patterns:
            if fnmatch.fnmatch(key, pattern):
                matched[key] = value
                break
    return matched
```

**Excluded vars** (never passthrough even in "all" mode):
```python
NEVER_PASSTHROUGH = {
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "PWD", "OLDPWD",
    "TERM", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
    "SSH_AUTH_SOCK", "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY",
    "LS_COLORS", "LANG", "LC_ALL",
}
```

**Tests**:
- `test_env_auto_matches_patterns` — API key patterns match correctly
- `test_env_auto_no_match` — non-matching vars excluded
- `test_env_all_mode` — passes everything except NEVER_PASSTHROUGH
- `test_env_none_mode` — only explicit extra_env
- `test_env_explicit_list` — only named vars
- `test_env_explicit_overrides_auto` — extra_env wins on conflict
- `test_env_pattern_fnmatch` — wildcards work (`*_API_KEY` matches `OPENAI_API_KEY`)

**Done when**: Env var passthrough works in all three modes with pattern matching.

---

### Task 1.7: Git Config Forwarding

**What**: Copy git configuration into the container so git operations work naturally.

**Add to `provisioner.py`**:

```python
async def provision_git(self, container: str, lifecycle: ContainerLifecycle) -> ProvisionResult:
    """Forward git configuration into the container."""
```

**What gets forwarded**:
1. `~/.gitconfig` — Copied to container user's home
2. `~/.gitconfig.local` — If it exists (common include pattern)
3. `~/.config/git/config` — XDG location alternative
4. `~/.ssh/known_hosts` — For git-over-SSH (just this file, NOT keys)

**Implementation**:
1. For each file that exists on the host, use `docker cp` to copy into container
2. Fix ownership inside the container: `chown` to the container user
3. Verify with `docker exec git config user.name` — should return the configured name

**Tests**:
- `test_git_forward_gitconfig` — copies .gitconfig
- `test_git_forward_gitconfig_local` — copies .gitconfig.local when present
- `test_git_forward_xdg` — handles XDG config location
- `test_git_forward_known_hosts` — copies known_hosts but NOT id_rsa etc.
- `test_git_no_config` — gracefully handles missing .gitconfig

**Done when**: Git config forwarding works. `git config user.name` inside the container returns the host user's name.

---

### Task 1.8: Container Tracking and Metadata Store

**What**: Ensure all managed containers are tracked persistently.

**File**: Add metadata management to `lifecycle.py`

**Storage location**: `~/.amplifier/containers/`

```
~/.amplifier/containers/
+-- registry.json
+-- containers/
    +-- amp-python-a3f2/
    |   +-- metadata.json
    +-- amp-rust-b7c3/
        +-- metadata.json
```

**`registry.json`**: Simple index of all container names with basic status.

**Operations**:
```python
class MetadataStore:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path.home() / ".amplifier" / "containers"

    def save(self, name: str, metadata: dict) -> None: ...
    def load(self, name: str) -> dict | None: ...
    def remove(self, name: str) -> None: ...
    def list_all(self) -> list[dict]: ...
    def update_registry(self) -> None: ...
```

**Resilience**: If metadata is lost but container still exists (has labels), list should still find it via `docker ps --filter label=amplifier.managed=true`. Belt and suspenders.

**Tests**:
- `test_metadata_save_load` — round-trip save and load
- `test_metadata_remove` — file cleaned up
- `test_metadata_list_all` — lists all saved containers
- `test_metadata_missing_graceful` — returns None, doesn't crash
- `test_registry_survives_manual_delete` — labels serve as backup

**Done when**: Container metadata persists across sessions. `containers(operation="list")` works even after restart.

---

### Task 1.9: Wire Up Tool Operations

**What**: Connect all the pieces — the tool's `execute` method dispatches to lifecycle, provisioner, runtime.

**File**: `modules/tool-containers/amplifier_module_tool_containers/__init__.py`

**Full tool_definitions schema** (for Phase 1 operations):
```python
@property
def tool_definitions(self) -> list[dict]:
    return [{
        "name": "containers",
        "description": (
            "Manage isolated container environments. "
            "Use for safe repo exploration, clean dev environments, "
            "parallel workloads, and any scenario requiring isolation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "preflight", "create", "exec",
                        "exec_interactive_hint",
                        "list", "status", "destroy", "destroy_all",
                        "copy_in", "copy_out",
                    ],
                },
                "container": {"type": "string"},
                "name": {"type": "string"},
                "image": {"type": "string"},
                "purpose": {"type": "string"},
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 300},
                "mounts": {"type": "array", "items": {"type": "object"}},
                "mount_cwd": {"type": "boolean", "default": True},
                "ports": {"type": "array", "items": {"type": "object"}},
                "env": {"type": "object"},
                "env_passthrough": {},  # string or array
                "forward_git": {"type": "boolean"},
                "forward_gh": {"type": "boolean"},
                "forward_ssh": {"type": "boolean"},
                "setup_commands": {"type": "array", "items": {"type": "string"}},
                "memory_limit": {"type": "string", "default": "4g"},
                "network": {"type": "string", "default": "bridge"},
                "persistent": {"type": "boolean", "default": False},
                "force": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
                "health_check": {"type": "boolean", "default": False},
                "host_path": {"type": "string"},
                "container_path": {"type": "string"},
            },
            "required": ["operation"],
        },
    }]
```

**Execute dispatch**:
```python
async def execute(self, tool_name: str, tool_input: dict) -> Any:
    op = tool_input["operation"]

    # Auto-preflight before first create
    if op == "create" and not self._preflight_passed:
        preflight = await self._op_preflight(tool_input)
        if not preflight["ready"]:
            return {
                "error": "Container runtime not ready",
                "preflight": preflight,
            }
        self._preflight_passed = True

    handler = getattr(self, f"_op_{op}", None)
    if not handler:
        return {"error": f"Unknown operation: {op}"}
    return await handler(tool_input)
```

**Create orchestration** (the _op_create method):
1. Parse `CreateParams` from input (merge with config defaults)
2. `lifecycle.create(params)` — start the container
3. `provisioner.provision_env_vars(...)` — env passthrough
4. `provisioner.provision_git(...)` — if forward_git
5. Run `setup_commands` via `lifecycle.exec_command()`
6. Return structured result with container name, connect command, and status

**File transfer operations**:
- `copy_in`: `docker cp {host_path} {container}:{container_path}`
- `copy_out`: `docker cp {container}:{container_path} {host_path}`

**Tests**:
- `test_create_full_flow` — integration test: create, verify provisioning, check metadata
- `test_create_auto_preflight` — first create triggers preflight
- `test_exec_dispatches` — exec calls lifecycle.exec_command
- `test_copy_in_out` — file transfer works
- `test_unknown_operation` — returns error

**Done when**: Full end-to-end flow works: `preflight` -> `create` -> `exec` -> `destroy`. This is the Phase 1 integration milestone.

---

### Task 1.10: Context Document — container-awareness.md

**What**: Write the thin root context that teaches the assistant how to use the containers tool.

**File**: `context/container-awareness.md`

**Content guidelines**:
- Keep it lean (~50-80 lines). This loads into every root session.
- Cover: what the tool does, when to use it, quick-start pattern
- Reference the `container-operator` agent for complex scenarios
- Teach the preflight-first pattern
- Teach the handoff pattern (exec_interactive_hint)
- List all operations briefly

**Key instruction patterns**:
1. Always run `preflight` before first `create` in a session (or rely on auto-preflight)
2. Use `purpose` parameter to let the tool choose smart defaults
3. For simple containers: use the tool directly
4. For complex multi-container setups: delegate to `container-operator`
5. After creating a container for the user: always provide the `exec_interactive_hint` command
6. Prefer `destroy` over leaving containers running when done

**Tests**: N/A (context document, not code)

**Done when**: An assistant with this context can competently use the containers tool for basic scenarios without additional guidance.

---

### Task 1.11: Integration Test — Full End-to-End

**What**: Verify the complete Phase 1 flow works with a real container runtime.

**Test file**: `tests/test_integration.py`

**Test scenarios** (require Docker/Podman — mark as integration tests):
1. **Preflight passes** — on a machine with Docker
2. **Create and destroy** — create container, verify it's running, destroy it, verify it's gone
3. **Exec command** — create container, run `echo hello`, verify output
4. **Env passthrough** — set `TEST_API_KEY=test123`, create with auto passthrough, verify inside container
5. **Git config forward** — create with forward_git, verify `git config user.name` inside container
6. **CWD mount** — create with mount_cwd, verify host files visible in container
7. **Copy in/out** — create container, copy file in, read it back, copy file out
8. **Metadata persistence** — create container, verify metadata exists, list returns it
9. **Interactive hint** — create container, get hint, verify it's a valid command

**Test infrastructure**:
- Fixture: `@pytest.fixture` that creates a container and destroys it after test
- Mark: `@pytest.mark.integration` — skip in unit-test-only CI
- Timeout: 60s per test (image pulls can be slow)

**Done when**: All integration tests pass on a machine with Docker or Podman.

---

## Phase 2: Convenience

> Goal: Full identity forwarding (GH, SSH, dotfiles), purpose-based smart defaults, interactive handoff, snapshots, specialist agent, and safety hooks.

### Task 2.1: GH CLI Auth Forwarding

**What**: Forward GitHub CLI authentication into containers.

**Add to `provisioner.py`**:

```python
async def provision_gh_auth(self, container: str, lifecycle: ContainerLifecycle) -> ProvisionResult:
    """Forward GH CLI authentication into the container."""
```

**Implementation**:
1. Check if `gh` CLI is available on host: `shutil.which("gh")`
2. If not, skip with informational message (not an error)
3. Extract token: `gh auth token` — capture stdout
4. If token extraction fails (not logged in), skip with guidance
5. Write `GH_TOKEN` and `GITHUB_TOKEN` env vars into container's env file
6. Check if `gh` is installed in container (`which gh`)
7. If yes, run `gh auth login --with-token` for full `gh` integration
8. If no, the env var alone enables `git clone` of private repos via HTTPS

**Security notes**:
- Token goes into the container's `/tmp/.amplifier_env` file and `.bashrc`
- Container is ephemeral by default — token dies with container
- Same security scope as the host — user already has this token
- Add to `metadata.json` provisioning record (flag only, NOT the token value)

**Tests**:
- `test_gh_auth_forward_success` — token extracted and injected
- `test_gh_auth_no_gh_cli` — gracefully skips, returns informational result
- `test_gh_auth_not_logged_in` — gracefully skips with login guidance
- `test_gh_auth_with_gh_in_container` — runs `gh auth login --with-token`
- `test_gh_auth_without_gh_in_container` — just sets env vars
- Integration test: `gh auth status` works inside container

**Done when**: Private repo cloning works inside the container via GH token forwarding.

---

### Task 2.2: SSH Key Forwarding

**What**: Opt-in bind-mount of SSH keys into containers.

**Add to `provisioner.py`**:

```python
async def provision_ssh(self, container: str, lifecycle: ContainerLifecycle) -> ProvisionResult:
    """Mount SSH directory into container (read-only)."""
```

**Implementation note**: Unlike git/gh forwarding which happens after container creation, SSH forwarding must happen at container creation time as a bind mount (`-v ~/.ssh:/home/user/.ssh:ro`). So this modifies `CreateParams` before the `docker run` command.

**Flow**:
1. Check if `~/.ssh` exists on host
2. Add `-v {home}/.ssh:/root/.ssh:ro` to create params (adjust target user path)
3. After container starts, fix permissions:
   - `chmod 700 /root/.ssh`
   - `chmod 600 /root/.ssh/id_*` (ignore errors for files that don't exist)
   - `chmod 644 /root/.ssh/*.pub` (public keys can be readable)
   - `chmod 644 /root/.ssh/known_hosts`
4. Verify with `ssh -T git@github.com` (should get "Hi username" even if exit code is 1)

**Tests**:
- `test_ssh_mount_added_to_create` — bind mount flag present in docker run args
- `test_ssh_permissions_fixed` — correct chmod calls made
- `test_ssh_no_ssh_dir` — gracefully skips
- `test_ssh_read_only` — mount is :ro
- Integration test: `ssh -T git@github.com` works inside container (if host has SSH keys)

**Done when**: SSH key forwarding works with read-only mount and correct permissions.

---

### Task 2.3: Purpose Profiles

**What**: Smart default selection based on intended purpose.

**File**: `modules/tool-containers/amplifier_module_tool_containers/images.py`

```python
class PurposeResolver:
    """Resolves purpose hints into container configuration."""

    PROFILES: dict[str, PurposeProfile] = { ... }

    def resolve(self, purpose: str, explicit_params: dict) -> ResolvedConfig:
        """Merge purpose defaults with explicit parameters. Explicit wins."""
```

**`PurposeProfile` dataclass**:
```python
@dataclass
class PurposeProfile:
    image: str
    packages: list[str] = field(default_factory=list)  # apt packages to install
    setup_commands: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    forward_git: bool = True
    forward_gh: bool = True
    forward_ssh: bool = False
    dotfiles: bool = True  # Apply dotfiles if configured
```

**Profiles to implement**:
- `python`: python:3.12-slim, install uv, create venv
- `node`: node:20-slim, enable corepack
- `rust`: rust:1-slim, build-essential, pkg-config, libssl-dev
- `go`: golang:1.22, go toolchain ready
- `general`: ubuntu:24.04, build-essential, git, curl, jq, tree, vim-tiny
- `amplifier`: python:3.12-slim, uv tool install amplifier, auto-forward all creds
- `clean`: ubuntu:24.04, NO dotfiles, NO forwarding

**Merge behavior**: Explicit parameters always override purpose defaults. Purpose only fills in what wasn't specified.

**Integration with create flow**:
1. If `purpose` is provided, resolve profile
2. Merge profile defaults with explicit params
3. Proceed with merged params

**Tests**:
- `test_resolve_python` — correct image, uv setup
- `test_resolve_amplifier` — correct image, amplifier install, creds forwarded
- `test_resolve_clean` — no forwarding, no dotfiles
- `test_explicit_overrides_purpose` — explicit image beats purpose default
- `test_unknown_purpose` — returns error or falls back to general
- `test_purpose_packages_installed` — setup_commands include apt install

**Done when**: `containers(create, purpose="python")` creates a Python-ready container with uv and venv.

---

### Task 2.4: Dotfiles Integration

**What**: Clone a dotfiles repo into containers and run the install script.

**Add to `provisioner.py`**:

```python
async def provision_dotfiles(
    self,
    container: str,
    lifecycle: ContainerLifecycle,
    repo: str | None = None,
    script: str | None = None,
    branch: str | None = None,
    target: str = "~/.dotfiles",
    inline: dict[str, str] | None = None,
    skip: bool = False,
) -> ProvisionResult:
    """Clone and apply dotfiles from a git repo or inline content."""
```

**Flow for repo-based dotfiles**:
1. If `skip=True` or no repo configured, skip
2. Clone repo into container: `git clone [--branch {branch}] {repo} {target}`
   - This runs AFTER gh/ssh forwarding, so private repos work
3. Find install script (resolution order): explicit > install.sh > setup.sh > bootstrap.sh > script/setup > Makefile > smart-symlink
4. Run the install script: `cd {target} && ./{script}` or `cd {target} && make`
5. Return result with script output

**Flow for inline dotfiles**:
1. For each `{path: content}` in `inline` dict:
2. Write file to container at `~/{path}` via `docker exec sh -c "cat > ~/path << 'DOTEOF'\n{content}\nDOTEOF"`

**Smart-symlink fallback** (when no install script found):
```python
COMMON_DOTFILES = [
    ".bashrc", ".bash_profile", ".bash_aliases",
    ".zshrc", ".zprofile",
    ".gitconfig", ".gitignore_global",
    ".vimrc", ".tmux.conf",
    ".inputrc", ".editorconfig",
]

# For each that exists in dotfiles repo, symlink to ~/
```

**Tests**:
- `test_dotfiles_clone_public_repo` — clones and runs install.sh
- `test_dotfiles_clone_private_repo` — works when gh auth is forwarded
- `test_dotfiles_script_resolution` — picks correct script from resolution order
- `test_dotfiles_inline` — writes inline content correctly
- `test_dotfiles_smart_symlink` — fallback when no script found
- `test_dotfiles_skip` — skip=True does nothing
- `test_dotfiles_no_repo_configured` — gracefully skips
- `test_dotfiles_script_failure` — returns error but doesn't fail container creation

**Done when**: Dotfiles integration works for both repo-based and inline approaches. Private repos work when GH auth is forwarded.

---

### Task 2.5: Snapshot and Restore

**What**: Save container state as named images and create containers from snapshots.

**Add to `lifecycle.py`**:

```python
async def snapshot(self, container: str, snapshot_name: str) -> SnapshotResult:
    """Commit container state as a new image."""

async def restore(self, snapshot_name: str, container_name: str | None = None, **kwargs) -> CreateResult:
    """Create a new container from a saved snapshot."""

async def list_snapshots(self) -> list[SnapshotInfo]:
    """List all saved snapshots."""

async def delete_snapshot(self, snapshot_name: str) -> None:
    """Remove a saved snapshot image."""
```

**Snapshot implementation**:
1. `docker commit {container} amplifier-snapshot:{snapshot_name}`
2. Save snapshot metadata to `~/.amplifier/containers/snapshots/{name}.json`
3. Return image ID and name

**Restore implementation**:
1. Create container with `image=amplifier-snapshot:{snapshot_name}` instead of stock image
2. All other create params work normally (mounts, ports, env, etc.)

**Tests**:
- `test_snapshot_creates_image` — docker commit called correctly
- `test_restore_uses_snapshot_image` — create uses snapshot image
- `test_list_snapshots` — returns saved snapshots
- `test_delete_snapshot` — removes image
- Integration: snapshot, destroy original, restore, verify state preserved

**Done when**: Users can save checkpoints of container state and restore from them.

---

### Task 2.6: Container Operator Agent

**What**: Specialist agent for complex container orchestration.

**File**: `agents/container-operator.md`

**Agent responsibilities**:
- Multi-container setup and coordination
- Troubleshooting container issues
- Complex provisioning workflows
- Service stack orchestration

**Agent gets**:
- The `tool-containers` module (declared in its frontmatter)
- Heavy context via `@containers:context/container-guide.md`
- Knowledge of all purpose profiles, all provisioning options, troubleshooting guides

**File**: `context/container-guide.md`

**Heavy context contents** (~200-300 lines):
- Full operation reference with all parameters
- Purpose profile details
- Dotfiles integration patterns
- Multi-container networking patterns
- Troubleshooting guide (common errors and fixes)
- Security model explanation
- Best practices for provisioning order
- The three interaction modes (puppet, handoff, hybrid)

**Tests**: N/A (agent + context docs)

**Done when**: Delegating to `container-operator` for "set up a full-stack dev environment with Postgres, Redis, and my app" produces a working multi-container setup.

---

### Task 2.7: Container Safety Hooks (Optional Behavior)

**What**: Approval gates for dangerous container operations.

**Files**:
- `behaviors/container-safety.yaml` — Optional behavior declaring the hook
- `modules/hooks-container-safety/pyproject.toml` — Module package
- `modules/hooks-container-safety/amplifier_module_hooks_container_safety/__init__.py` — Hook implementation

**Hook events to intercept**:
- `tool:pre` on `containers` tool — inspect the operation parameters

**Approval-required scenarios**:
- `create` with `gpu=True`
- `create` with `network="host"`
- `create` with mounts to sensitive paths (`/`, `/etc`, `/var`, `/root`, `/home`)
- `create` with `forward_ssh=True`
- `env_passthrough="all"`
- `destroy_all`

**Session cleanup**:
- `session:end` event — destroy all non-persistent containers from this session

**Max containers limit**:
- Track container count per session
- Deny `create` if `max_containers_per_session` exceeded

**`behaviors/container-safety.yaml`**:
```yaml
bundle:
  name: behavior-container-safety
  version: 0.1.0
  description: Safety policies for container operations

hooks:
  - module: hooks-container-safety
    source: "containers:modules/hooks-container-safety"
    config:
      require_approval_for:
        - gpu_access
        - host_network
        - sensitive_mounts
        - ssh_forwarding
        - all_env_passthrough
        - destroy_all
      auto_cleanup_on_session_end: true
      max_containers_per_session: 10
```

**Tests**:
- `test_hook_blocks_privileged` — approval required for GPU
- `test_hook_blocks_host_network` — approval required for host network
- `test_hook_blocks_sensitive_mount` — approval required for /etc mount
- `test_hook_allows_normal_create` — normal create passes through
- `test_hook_session_cleanup` — containers destroyed on session end
- `test_hook_max_containers` — denies create beyond limit

**Done when**: Safety hook behavior works as an optional overlay. Including it adds approval gates without changing the core tool behavior.

---

## Phase 3: Multi-Container

> Goal: Support service stacks, container networking, compose pass-through, and Amplifier-in-container.

### Task 3.1: Network Management

**What**: Create and manage Docker networks for container-to-container communication.

**Add operations to tool**:
- `create_network(name)` — `docker network create {name}`
- `destroy_network(name)` — `docker network rm {name}`
- `list_networks()` — `docker network ls --filter label=amplifier.managed=true`

**Modify `create`**: Accept `network` parameter as either:
- `"bridge"` (default) — standard isolated network
- Named network — attach to existing network created by `create_network`

**Track networks** in metadata store alongside containers.

**Tests**:
- `test_create_network` — network created with labels
- `test_create_on_named_network` — container attached to named network
- `test_container_name_resolution` — containers on same network can reach each other by name
- `test_destroy_network` — network removed, handles in-use error
- Integration: two containers on same network, one curls the other

**Done when**: Service stacks work — containers on the same network can communicate by name.

---

### Task 3.2: Docker Compose Pass-Through

**What**: Support `compose_up` and `compose_down` for users with existing compose files.

**Add operations**:
- `compose_up(compose_file, project_name=None)` — `docker compose -f {file} [-p {name}] up -d`
- `compose_down(project_name)` — `docker compose [-p {name}] down`
- `compose_status(project_name)` — `docker compose [-p {name}] ps --format json`

**Implementation notes**:
- Detect `docker compose` vs `docker-compose` (old standalone)
- Add standard labels to compose project for tracking
- Track compose projects in metadata store

**Tests**:
- `test_compose_up` — starts services from compose file
- `test_compose_down` — stops and removes services
- `test_compose_status` — lists running services
- Integration: create a simple compose file (nginx + redis), bring up, verify, tear down

**Done when**: Users with existing docker-compose.yml files can use them through the tool.

---

### Task 3.3: Amplifier-in-Container Purpose Profile

**What**: Polish the `"amplifier"` purpose profile for running Amplifier inside containers.

**Enhanced amplifier profile**:
1. Base: python:3.12-slim + git + curl + jq
2. Install uv: `pip install uv`
3. Install amplifier: `uv tool install amplifier` (or specific version)
4. Forward ALL credential types: API keys, git, GH auth
5. Forward amplifier settings if they exist: `~/.amplifier/settings.yaml`
6. Set up proper PATH for uv tools

**Additional create parameter** (amplifier-specific):
- `amplifier_bundle` — optional bundle name/URI to configure inside the container
- `amplifier_settings` — optional, forward host's amplifier settings

**Tests**:
- `test_amplifier_profile_installs_amplifier` — amplifier CLI available inside container
- `test_amplifier_profile_forwards_creds` — all API keys passed through
- `test_amplifier_profile_forwards_settings` — settings.yaml copied
- Integration: create amplifier container, run `amplifier run "echo hello"`, verify output

**Done when**: `containers(create, purpose="amplifier")` produces a container where `amplifier run "do something"` just works.

---

## Phase 4: Polish

> Goal: Auto-detection, curated images, background execution, GPU support.

### Task 4.1: Try-Repo Auto-Detection

**What**: The `"try-repo"` purpose inspects a repository to choose the right profile.

**Add to `images.py`**:

```python
async def detect_repo_purpose(self, repo_url: str, lifecycle: ContainerLifecycle) -> str:
    """Clone repo (shallow), inspect files, return purpose string."""
```

**Detection rules** (in priority order):
1. `Cargo.toml` present → "rust"
2. `pyproject.toml` or `setup.py` or `requirements.txt` → "python"
3. `package.json` → "node"
4. `go.mod` → "go"
5. `Dockerfile` present → use that Dockerfile directly
6. Fallback → "general"

**Additional `create` parameter**:
- `repo_url` — Git URL to clone (used with purpose="try-repo")

**Flow**: Shallow clone into temp dir → detect → create container with detected purpose → full clone inside container → run setup.

**Tests**:
- `test_detect_python_repo` — pyproject.toml triggers python
- `test_detect_node_repo` — package.json triggers node
- `test_detect_rust_repo` — Cargo.toml triggers rust
- `test_detect_dockerfile` — Dockerfile uses custom image
- `test_detect_fallback` — unknown repo gets general

**Done when**: `containers(create, purpose="try-repo", repo_url="https://github.com/user/repo")` auto-detects the right environment.

---

### Task 4.2: Background Execution with Polling

**What**: For long-running commands, run in background and poll for completion.

**Add operations**:
- `exec_background(container, command)` — returns `job_id`
- `exec_poll(container, job_id)` — returns status, partial output
- `exec_cancel(container, job_id)` — kill the background process

**Implementation**: Start command with nohup, redirect output to a temp file, return PID as job_id. Poll reads the temp file. Cancel sends SIGTERM.

**Tests**:
- `test_background_exec_returns_job_id`
- `test_poll_running_job` — returns partial output
- `test_poll_completed_job` — returns full output and exit code
- `test_cancel_job` — process killed

**Done when**: Long-running commands don't block the tool. Agent can start a build and check on it later.

---

### Task 4.3: GPU Passthrough

**What**: Support `--gpus all` for ML/AI workloads.

**Modify create**:
- If `gpu=True`, add `--gpus all` to docker run
- Preflight: check `nvidia-smi` or `docker info --format '{{.Runtimes}}'` for nvidia runtime

**Tests**:
- `test_gpu_flag_added` — --gpus all in docker run args
- `test_gpu_preflight_check` — nvidia runtime detection
- Integration (GPU hosts only): create GPU container, run `nvidia-smi`

**Done when**: `containers(create, gpu=True)` works on hosts with NVIDIA Docker runtime.

---

### Task 4.4: Curated Base Image

**What**: Optional pre-built image with common tools for faster startup.

**File**: `images/amplifier-base/Dockerfile`

**Contents**:
- Base: python:3.12-slim
- Common tools: git, curl, wget, jq, tree, vim-tiny, less, make, build-essential
- uv pre-installed
- Non-root user `amplifier`
- Proper locale setup

**Not included in the Dockerfile**: Application-specific tools (those come from purpose profiles at runtime).

**Distribution**: Push to GitHub Container Registry (ghcr.io/microsoft/amplifier-base).

**Integration**: Purpose profiles can reference this image instead of stock images for faster startup.

**Tests**:
- `test_dockerfile_builds` — image builds successfully
- `test_image_has_tools` — all expected tools present
- `test_image_nonroot_user` — runs as non-root by default

**Done when**: Optional curated image available for faster container startup.

---

## Testing Strategy

### Test Categories

| Category | Location | Requires Docker | CI |
|----------|----------|----------------|-----|
| Unit tests | `tests/unit/` | No | Yes |
| Integration tests | `tests/integration/` | Yes | Needs Docker-in-Docker |
| End-to-end tests | `tests/e2e/` | Yes | Manual/nightly |

### Unit Test Approach
- Mock `ContainerRuntime.run()` to avoid real Docker calls
- Test parameter building, config merging, metadata management
- Test pattern matching, purpose resolution, provisioning logic

### Integration Test Approach
- Require real Docker/Podman (skip if not available)
- Use small images (alpine:3.19) for speed
- Clean up all containers in test teardown
- Timeout per test: 60s

### Fixtures

```python
@pytest.fixture
async def runtime():
    """Real container runtime (skip if unavailable)."""
    rt = ContainerRuntime()
    if not await rt.detect():
        pytest.skip("No container runtime available")
    return rt

@pytest.fixture
async def container(runtime):
    """Create and cleanup a test container."""
    lifecycle = ContainerLifecycle(runtime, {})
    result = await lifecycle.create(CreateParams(
        name=f"test-{uuid4().hex[:8]}",
        image="alpine:3.19",
    ))
    yield result
    await lifecycle.destroy(result.name, force=True)
```
