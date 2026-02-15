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

> **Phase 1 Status**: COMPLETE — All tasks implemented and tested (54 tests passing).
> Kept here as reference for patterns and implementation details.

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

---

## Phase 2: Production Readiness

> Goal: Fix implementation learnings from Phase 1 and add the features that make containers truly useful day-to-day: proper file ownership, visibility into what happened during setup, faster startup, try-repo auto-detection, and non-blocking long commands.

> **Extraction note**: `__init__.py` is currently ~926 lines and will grow significantly with Phase 2 additions (provisioning report, caching, try-repo, background exec). As part of Task 2.2 (UID mapping) — which already touches all the provisioning methods — extract the provisioning methods (`_provision_git`, `_provision_gh_auth`, `_fix_ssh_permissions`, `_provision_dotfiles`, `_provision_dotfiles_inline`, and the new `_get_container_home`) from `__init__.py` into an expanded `provisioner.py` class (which currently only has env var matching functions). This keeps `__init__.py` focused on the tool interface and operation dispatch while provisioning logic lives in its own module.

### Task 2.1: Development Infrastructure

**What**: Root-level pyproject.toml for dev dependencies and proper pytest configuration.

**Files to create/modify**:
- `pyproject.toml` (root) — Dev dependencies and pytest configuration

**Root `pyproject.toml`**:
```toml
[project]
name = "amplifier-bundle-containers"
version = "0.1.0"
description = "Container management bundle for Amplifier"
requires-python = ">=3.11"
license = "MIT"

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: tests requiring Docker/Podman (deselect with '-m not integration')",
]
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.pyright]
pythonVersion = "3.11"
```

**Modify `tests/conftest.py`**: Add sys.path setup so pytest discovers the module without hacking:
```python
import sys
from pathlib import Path

# Add tool module to sys.path for test discovery
sys.path.insert(0, str(Path(__file__).parent.parent / "modules" / "tool-containers"))
```

**Tests**: Run `pytest tests/ -v` — all 54 existing tests should still pass.

**Done when**: `pytest` works from repo root without sys.path hacks in individual test files. Integration marker registered (no warning).

---

### Task 2.2: Host UID/GID Mapping

**What**: Default to host user's UID/GID when mounting volumes, so files created in the container have correct ownership on the host.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — `_op_create` method, `_get_container_home()` helper
- `modules/tool-containers/amplifier_module_tool_containers/provisioner.py` — Extract provisioning methods from `__init__.py` (per Phase 2 extraction note): `_provision_git`, `_provision_gh_auth`, `_fix_ssh_permissions`, `_provision_dotfiles`, `_provision_dotfiles_inline`. Update all `/root/` paths to use dynamic home directory.

**Changes**:

1. Add `user` parameter to `CreateParams` dataclass:
```python
user: str | None = None  # Default: auto-detect host UID:GID
```

2. In `_op_create`, compute user mapping:
```python
import os

# Default to host UID:GID for proper file ownership on mounted volumes
if inp.get("user") is None and inp.get("mount_cwd", True):
    uid = os.getuid()
    gid = os.getgid()
    user_flag = f"{uid}:{gid}"
else:
    user_flag = inp.get("user")  # Explicit override or None (root)

if user_flag:
    args.extend(["--user", user_flag])
```

3. Update all provisioning methods to detect the home directory inside the container instead of hardcoding `/root/`. Add `_get_container_home()` as a method on the `ContainersTool` class in `__init__.py` (it needs access to `self.runtime.run()`):
```python
async def _get_container_home(self, container: str) -> str:
    """Get the home directory of the container user."""
    result = await self.runtime.run(
        "exec", container, "/bin/sh", "-c", "echo $HOME", timeout=5
    )
    home = result.stdout.strip()
    return home if home else "/root"
```

4. Replace all hardcoded `/root/` paths in `_provision_git`, `_provision_gh_auth`, `_fix_ssh_permissions`, `_provision_dotfiles`, `_provision_dotfiles_inline` with calls to `_get_container_home()`.

5. Add `user` to the tool's input_schema properties:
```python
"user": {"type": "string", "description": "Container user (default: host UID:GID for mounted volumes, 'root' for root access)"},
```

**Edge cases**:
- When `mount_cwd=False` and no explicit mounts, skip UID mapping (no volume permission issues)
- When `user="root"` is explicitly set, use root (some purpose profiles may need this)
- The `--user` flag means the container user may not have a proper home dir — use `HOME` env var detection
- SSH bind mount with non-root user: permissions may differ, test carefully

**Tests**:
- `test_uid_gid_mapping_default` — when mount_cwd=True, --user flag added with host UID:GID
- `test_uid_gid_mapping_no_mount` — when mount_cwd=False and no mounts, no --user flag
- `test_uid_gid_mapping_explicit_root` — user="root" overrides default mapping
- `test_uid_gid_mapping_explicit_user` — user="1000:1000" used as-is
- `test_provisioning_uses_container_home` — provisioning paths adapt to non-root home
- Integration: create with mount_cwd, touch a file inside /workspace, verify host ownership matches current user

**Done when**: Files created inside mounted volumes have correct host user ownership by default.

---

### Task 2.3: Provisioning Report

**What**: Return a structured report from `create` showing the status of each provisioning step, so the caller doesn't need to investigate.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py`

**Changes**:

1. Define a `ProvisioningStep` structure:
```python
@dataclass
class ProvisioningStep:
    name: str
    status: str  # "success", "skipped", "failed", "partial"
    detail: str
    error: str | None = None
```

2. Each provisioning method returns a `ProvisioningStep` instead of silently succeeding/failing:
```python
async def _provision_git(self, container: str) -> ProvisioningStep:
    """Copy git configuration into the container."""
    home = Path.home()
    gitconfig = home / ".gitconfig"
    if not gitconfig.exists():
        return ProvisioningStep("forward_git", "skipped", "No .gitconfig found on host")
    
    # ... do the work ...
    
    return ProvisioningStep("forward_git", "success", "Copied .gitconfig, known_hosts")
```

3. `_op_create` collects all steps into the report:
```python
report = []
if inp.get("forward_git", True):
    report.append(await self._provision_git(name))
else:
    report.append(ProvisioningStep("forward_git", "skipped", "Not requested"))

# ... same for gh, ssh, dotfiles, purpose setup, setup_commands ...

# Include in result
return {
    "success": True,
    "container": name,
    "provisioning_report": [
        {"name": s.name, "status": s.status, "detail": s.detail, "error": s.error}
        for s in report
    ],
    # ... other fields ...
}
```

4. For `setup_commands`, track each command individually:
```python
cmd_results = []
for i, cmd in enumerate(setup_commands):
    result = await self.runtime.run("exec", container, "/bin/sh", "-c", cmd, timeout=300)
    if result.returncode != 0:
        cmd_results.append({"command": cmd, "status": "failed", "error": result.stderr.strip()})
    else:
        cmd_results.append({"command": cmd, "status": "success"})

all_ok = all(r["status"] == "success" for r in cmd_results)
report.append(ProvisioningStep(
    "setup_commands",
    "success" if all_ok else "partial",
    f"{sum(1 for r in cmd_results if r['status'] == 'success')}/{len(cmd_results)} commands succeeded",
    error=None if all_ok else str([r for r in cmd_results if r["status"] == "failed"]),
))
```

**Tests**:
- `test_report_all_success` — all provisioning steps report success
- `test_report_git_skipped` — forward_git=False shows skipped
- `test_report_git_no_config` — no .gitconfig shows skipped with detail
- `test_report_gh_not_installed` — no gh CLI shows skipped with guidance
- `test_report_setup_command_failure` — partial status when some commands fail
- `test_report_dotfiles_clone_failed` — failed status with error detail
- Integration: create with forward_git=True on a machine with .gitconfig, verify report shows success

**Done when**: Every `create` returns a provisioning report. The caller never needs to `exec` into the container to check if credentials were set up correctly.

---

### Task 2.4: Local Image Caching

**What**: Cache provisioned container state locally to avoid repeating slow package installs on subsequent creates with the same purpose.

**Files to modify/create**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py`
- `modules/tool-containers/amplifier_module_tool_containers/images.py`

**How it works**:

1. After a successful `create` with a purpose profile, commit the container state as a local cached image:
```python
cache_tag = f"amplifier-cache:{purpose}"
await self.runtime.run("commit", container_name, cache_tag, timeout=120)
```

2. On subsequent creates with the same purpose, check for the cached image first:
```python
async def _get_cached_image(self, purpose: str) -> str | None:
    """Check if a locally cached image exists and is current for this purpose."""
    cache_tag = f"amplifier-cache:{purpose}"
    result = await self.runtime.run(
        "image", "inspect", "--format",
        "{{index .Config.Labels \"amplifier.cache.version\"}}",
        cache_tag, timeout=10,
    )
    if result.returncode != 0:
        return None  # No cached image
    
    # Verify cache version matches current profile definition
    profile = PURPOSE_PROFILES.get(purpose)
    if profile:
        expected_hash = hashlib.md5(str(profile).encode()).hexdigest()[:8]
        cached_hash = result.stdout.strip()
        if cached_hash != expected_hash:
            return None  # Cache is stale, profile definition changed
    
    return cache_tag
```

3. When a cached image is found, skip the package install setup_commands from the purpose profile (they're already baked in). Still run: env provisioning, credential forwarding, dotfiles, user's explicit setup_commands.

4. Add `cache_bust` parameter to force a fresh build:
```python
"cache_bust": {"type": "boolean", "default": False, "description": "Ignore cached image, build fresh"},
```

5. Add `cache_clear` operation to remove cached images:
```python
async def _op_cache_clear(self, inp: dict) -> dict:
    """Remove locally cached purpose images."""
    purpose = inp.get("purpose")  # Optional: clear specific or all
    if purpose:
        await self.runtime.run("rmi", f"amplifier-cache:{purpose}", timeout=15)
    else:
        # List and remove all amplifier-cache:* images
        ...
```

**Cache invalidation**: Cached images should include a version label so we can detect when the purpose profile definition has changed:
```python
# When committing cache, include version label for invalidation:
version_hash = hashlib.md5(str(profile).encode()).hexdigest()[:8]
cache_tag = f"amplifier-cache:{purpose}"
await self.runtime.run(
    "commit",
    "--change", f'LABEL amplifier.cache.version={version_hash}',
    container_name, cache_tag,
    timeout=120,
)
```

**Tests**:
- `test_cache_created_after_purpose_create` — cache image exists after first create
- `test_cache_used_on_second_create` — second create with same purpose uses cached image
- `test_cache_bust_ignores_cache` — cache_bust=True forces fresh build
- `test_cache_clear_specific` — removes one cached image
- `test_cache_clear_all` — removes all cached images
- `test_cache_not_created_without_purpose` — no caching for purposeless creates
- Integration: first create with purpose="general" is slow (installs packages), second create is fast

**Done when**: Second container creation with the same purpose is significantly faster than the first.

---

### Task 2.5: Try-Repo Auto-Detection

**What**: The `"try-repo"` purpose inspects a repository to choose the right profile, clones it into the container, and runs setup.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/images.py`
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py`

**Add to images.py**:
```python
# Detection rules in priority order
REPO_MARKERS = [
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
    Clone is done via asyncio.create_subprocess_exec("git", ...) into a
    temp directory that is cleaned up after inspection.
    """
```

**Detection flow**:
1. Shallow clone on the HOST into a temp directory: `git clone --depth=1 {repo_url} {tmpdir}`
2. Inspect files against REPO_MARKERS
3. Generate repo-specific setup hints:
   - If `pyproject.toml` exists: `pip install -e ".[dev]"` or `uv pip install -e ".[dev]"`
   - If `package.json` exists: `npm install`
   - If `Cargo.toml` exists: `cargo build`
   - If `Makefile` exists: add `make` as a hint
4. Clean up temp directory
5. Return (purpose, setup_hints)

**Add `repo_url` to create params and tool schema**:
```python
"repo_url": {"type": "string", "description": "Git URL to clone (used with purpose='try-repo')"},
```

**Modify `_op_create` for try-repo**:
```python
if purpose == "try-repo":
    repo_url = inp.get("repo_url")
    if not repo_url:
        return {"error": "repo_url is required when purpose is 'try-repo'"}
    detected_purpose, setup_hints = await detect_repo_purpose(repo_url)
    inp["purpose"] = detected_purpose
    # Add clone + setup to setup_commands
    inp.setdefault("setup_commands", [])
    inp["setup_commands"] = [
        f"git clone {repo_url} /workspace/repo",
        "cd /workspace/repo",
    ] + setup_hints + inp["setup_commands"]
```

**Tests**:
- `test_detect_python_pyproject` — pyproject.toml triggers python purpose
- `test_detect_node_package_json` — package.json triggers node
- `test_detect_rust_cargo` — Cargo.toml triggers rust
- `test_detect_go_mod` — go.mod triggers go
- `test_detect_fallback_general` — no markers triggers general
- `test_detect_priority_order` — Cargo.toml wins over package.json if both exist
- `test_try_repo_requires_url` — error if repo_url missing
- `test_try_repo_adds_clone_command` — setup_commands include git clone
- Integration: create with purpose="try-repo" and a real public GitHub repo, verify repo cloned inside container

**Done when**: `containers(create, purpose="try-repo", repo_url="https://github.com/user/repo")` auto-detects the language, creates the right container, and clones the repo.

---

### Task 2.6: Background Execution with Polling

**What**: Run long commands without blocking the tool. Agent can start a build and check on it later.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py`

**Add operations**:
- `exec_background` — Start a command in the background, return a job ID
- `exec_poll` — Check status and get partial output of a background job
- `exec_cancel` — Kill a background job

**Implementation**:
```python
async def _op_exec_background(self, inp: dict) -> dict:
    container = inp["container"]
    command = inp["command"]
    job_id = uuid.uuid4().hex[:8]
    
    # Run command in background, save PID and exit code to temp files
    bg_cmd = (
        f"(/bin/sh -c '{command}'; echo $? > /tmp/amp-job-{job_id}.exit) "
        f"> /tmp/amp-job-{job_id}.out 2>&1 & "
        f"echo $! > /tmp/amp-job-{job_id}.pid && cat /tmp/amp-job-{job_id}.pid"
    )
    result = await self.runtime.run("exec", container, "/bin/sh", "-c", bg_cmd, timeout=10)
    pid = result.stdout.strip()
    
    return {"job_id": job_id, "pid": pid, "container": container, "command": command}

async def _op_exec_poll(self, inp: dict) -> dict:
    container = inp["container"]
    job_id = inp["job_id"]
    
    # Check if process is still running
    pid_check = await self.runtime.run(
        "exec", container, "/bin/sh", "-c",
        f"kill -0 $(cat /tmp/amp-job-{job_id}.pid 2>/dev/null) 2>/dev/null && echo running || echo done",
        timeout=5,
    )
    running = "running" in pid_check.stdout
    
    # Get output (tail for partial, cat for complete)
    output = await self.runtime.run(
        "exec", container, "/bin/sh", "-c",
        f"tail -100 /tmp/amp-job-{job_id}.out 2>/dev/null",
        timeout=5,
    )
    
    # Get exit code if done
    exit_code = None
    if not running:
        ec = await self.runtime.run(
            "exec", container, "/bin/sh", "-c",
            f"cat /tmp/amp-job-{job_id}.exit 2>/dev/null",
            timeout=5,
        )
        exit_code = int(ec.stdout.strip()) if ec.stdout.strip().isdigit() else None
    
    return {
        "job_id": job_id,
        "running": running,
        "output": output.stdout,
        "exit_code": exit_code,
    }

async def _op_exec_cancel(self, inp: dict) -> dict:
    container = inp["container"]
    job_id = inp["job_id"]
    
    await self.runtime.run(
        "exec", container, "/bin/sh", "-c",
        f"kill $(cat /tmp/amp-job-{job_id}.pid 2>/dev/null) 2>/dev/null",
        timeout=5,
    )
    return {"job_id": job_id, "cancelled": True}
```

**Add to tool_definitions operation enum**: `"exec_background"`, `"exec_poll"`, `"exec_cancel"`

**Add to input_schema**:
```python
"job_id": {"type": "string", "description": "Background job ID (for exec_poll/exec_cancel)"},
```

**Tests**:
- `test_exec_background_returns_job_id` — returns a job_id string
- `test_exec_poll_running` — reports running=True while command executes
- `test_exec_poll_completed` — reports running=False with exit_code after completion
- `test_exec_poll_output` — returns partial output
- `test_exec_cancel` — kills the background process
- Integration: start `sleep 5` in background, poll shows running, wait, poll shows done

**Done when**: Agent can start a long-running command, do other work, and check back for results.

---

### Task 2.7: Integration Tests for Phase 2 Features

**What**: Integration tests covering UID mapping, provisioning report, image caching, try-repo, and background exec.

**File**: `tests/integration/test_phase2.py`

**Tests**:
1. `test_uid_mapping_file_ownership` — create with mount_cwd, write file inside container, verify host file owned by current user
2. `test_provisioning_report_present` — create returns provisioning_report with expected steps
3. `test_provisioning_report_git_success` — forward_git step shows success when .gitconfig exists
4. `test_image_cache_speedup` — first purpose create is slower, second is faster (check for cache image existence)
5. `test_try_repo_public` — create with purpose="try-repo" and a known public repo (e.g., a small test fixture repo), verify repo cloned inside
6. `test_background_exec_lifecycle` — exec_background, poll while running, poll after done, verify exit code
7. `test_dotfiles_integration` — create with dotfiles_inline, verify files exist in container

**Done when**: All Phase 2 integration tests pass alongside the 54 existing tests.

---

## Phase 3: Two-Phase User Model + Amplifier-in-Container

> Goal: Refactor the user model so containers run as root (full admin for setup) while exec commands run as the mapped host user (correct file ownership). Then polish the amplifier purpose profile for the parallel-agents use case.

> **Key insight from Phase 2 review**: The current `--user UID:GID` on `docker run` prevents `apt-get install` and `pip install` from working during setup because the non-root user can't write to system paths. Additionally, `--security-opt=no-new-privileges` (our security hardening) prevents sudo from working inside containers. The solution: run the container as root, create a user matching the host UID:GID inside, run setup as root, then exec user commands with `docker exec --user UID:GID`.

### Task 3.1: Refactor to Two-Phase User Model

**What**: Move `--user` from `docker run` to `docker exec`. Container runs as root (setup works), exec commands run as mapped user (file ownership correct). Add `as_root` parameter for post-setup admin access.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — `_op_create`, `_op_exec`, `_op_exec_background`
- `modules/tool-containers/amplifier_module_tool_containers/provisioner.py` — Target user home for provisioning
- `tests/unit/test_provisioner.py` — Update UID mapping tests
- `tests/unit/test_tool.py` — Update exec tests

**Architecture change**:
```
BEFORE (Phase 2):
  docker run --user 1000:1000 ...     ← Non-root, setup fails
  docker exec container apt-get ...   ← Inherits non-root → FAILS

AFTER (Phase 3):
  docker run ... (no --user)          ← Root, setup works
  docker exec --user 1000:1000 container python app.py  ← Mapped user
  docker exec container pip install X                    ← Root (as_root)
```

**Security adjustment**: Remove `--cap-drop=ALL` from `_op_create`. Docker's default capability set (~14 capabilities) is sufficient security for ephemeral dev containers. The primary security control `--security-opt=no-new-privileges` is preserved. This change is required because `--cap-drop=ALL` prevents root from running `apt-get install`, `useradd`, and `chown` — all needed for the two-phase model.

**Changes**:

1. **Remove `--user` from `docker run` in `_op_create`**:
   - Remove the current UID/GID mapping block that adds `--user` to run args
   - Instead, compute and store `exec_user` in metadata:
     ```python
     exec_user = None
     if inp.get("user") is None and (inp.get("mount_cwd", True) or inp.get("mounts")):
         exec_user = f"{os.getuid()}:{os.getgid()}"
     elif inp.get("user") and inp.get("user") != "root":
         exec_user = inp.get("user")
     ```
   - Store `exec_user` in metadata.json
   - Also remove `"--cap-drop=ALL"` from the security hardening args (keep `--security-opt=no-new-privileges`, `--memory`, `--pids-limit`)

2. **Create user inside container during setup** (after container starts, before other setup):
   ```python
   if exec_user:
       uid, gid = exec_user.split(":")
       user_cmds = [
           f"groupadd -g {gid} -o hostuser 2>/dev/null || true",
           f"useradd -u {uid} -g {gid} -m -s /bin/bash -o hostuser 2>/dev/null || true",
       ]
       for cmd in user_cmds:
           await self.runtime.run("exec", name, "/bin/sh", "-c", cmd, timeout=30)
   ```

3. **Setup commands and provisioning run as root** (no change needed — container is root, exec without --user is root).

4. **Provisioning targets the created user's home**:
   - When `exec_user` is set, provision to `/home/hostuser` instead of detecting via `$HOME`
   - Update `ContainerProvisioner` to accept a `target_home` parameter

5. **Chown workspace after setup**:
   ```python
   if exec_user:
       await self.runtime.run(
           "exec", name, "/bin/sh", "-c",
           f"chown -R {exec_user} /workspace 2>/dev/null || true",
           timeout=30,
       )
   ```

6. **Modify `_op_exec` to use exec_user by default**:
   ```python
   async def _op_exec(self, inp):
       container = inp["container"]
       metadata = self.store.load(container)
       exec_user = None if inp.get("as_root", False) else (metadata or {}).get("exec_user")
       
       # Build args with --user in the caller (see item 7)
       exec_args = ["exec"]
       if exec_user:
           exec_args.extend(["--user", exec_user])
       exec_args.extend([container, "/bin/sh", "-c", inp["command"]])
       result = await self.runtime.run(*exec_args, timeout=inp.get("timeout", 300))
   ```

7. **Build exec args with `--user` in the callers** (do NOT modify `runtime.py`):
   Keep `runtime.run()` as a simple command runner. Instead, build the args list in `_op_exec` and `_op_exec_background`:
   ```python
   # In _op_exec:
   exec_args = ["exec"]
   if exec_user:
       exec_args.extend(["--user", exec_user])
   exec_args.extend([container, "/bin/sh", "-c", command])
   result = await self.runtime.run(*exec_args, timeout=timeout)
   ```
   This avoids leaking Docker-exec semantics into the generic runtime abstraction.

8. **Add `as_root` to tool schema**:
   ```python
   "as_root": {
       "type": "boolean",
       "default": False,
       "description": "Run command as root instead of mapped user (for package installation, system changes)",
   },
   ```

9. **Update `_op_exec_background` to use exec_user** (but NOT `_op_exec_poll` or `_op_exec_cancel`):
   `exec_background` starts the actual command, so it should run as the mapped user by default (with `as_root` override). `exec_poll` and `exec_cancel` only read temp files and send signals — these work fine as root and don't need user mapping.

10. **Update `_op_exec_interactive_hint`** to include the user flag:
    ```python
    # If exec_user is set, hint should include --user
    if exec_user:
        return {"command": f"{runtime} exec -it --user {exec_user} {container} {shell}", ...}
    ```

**Tests to rewrite** (these currently assert `--user` on docker run or mock create flows without useradd/chown):
- `test_uid_gid_mapping_default` — Change: verify exec_user stored in metadata instead of --user in run args
- `test_uid_gid_mapping_no_mount` — Change: verify no exec_user in metadata
- `test_uid_gid_mapping_explicit_root` — Change: verify no exec_user in metadata
- `test_uid_gid_mapping_explicit_user` — Change: verify exec_user="1000:1000" in metadata
- `test_create_returns_provisioning_report` — Change: mock must handle useradd/chown exec calls
- `test_provisioning_report_setup_command_partial` — Change: same mock update
- `test_create_result_includes_cache_used` — Change: same mock update
- `test_create_uses_cached_image` — Change: same mock update
- `test_try_repo_adds_clone_to_setup` — Change: same mock update

**New tests to write**:
- `test_create_no_user_flag_on_run` — docker run args do NOT contain --user
- `test_create_stores_exec_user_in_metadata` — metadata has exec_user field
- `test_create_creates_hostuser` — useradd command is called during setup
- `test_create_chowns_workspace` — chown command runs after setup
- `test_exec_uses_exec_user` — docker exec includes --user from metadata
- `test_exec_as_root_skips_user` — as_root=True runs without --user
- `test_exec_no_mounts_no_user` — when mount_cwd=False, no exec_user, runs as root
- `test_exec_interactive_hint_includes_user` — hint command includes --user when exec_user set
- `test_exec_background_uses_exec_user` — background exec respects mapped user
- `test_create_no_cap_drop_all` — verify --cap-drop=ALL is NOT in docker run args
- Integration: create with mount_cwd, touch file via exec (should be host-owned), install package via exec with as_root=True (should work)

**Done when**: Container runs as root, setup commands (apt-get, pip) work. Exec commands create files with correct host ownership. `as_root=True` gives admin access. All security hardening preserved.

---

### Task 3.2: Amplifier Purpose Profile Polish

**What**: Make `purpose="amplifier"` production-ready for running Amplifier inside containers. Now works correctly because setup runs as root.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/images.py` — Update amplifier profile
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — Add amplifier-specific provisioning
- `modules/tool-containers/amplifier_module_tool_containers/provisioner.py` — Add settings forwarding

**Enhanced profile**:
1. Forward `~/.amplifier/settings.yaml` if it exists
2. Forward `~/.amplifier/settings.local.yaml` if it exists  
3. Accept `amplifier_bundle` parameter — bundle name/URI to configure inside the container
4. Accept `amplifier_version` parameter — pin amplifier version (default: latest)
5. Install `uv` tools to a shared location accessible by both root and the mapped user:
   ```python
   # In amplifier profile setup_commands:
   "pip install --quiet uv",
   "UV_TOOL_BIN_DIR=/usr/local/bin uv tool install amplifier",
   ```
   Using `UV_TOOL_BIN_DIR=/usr/local/bin` installs the amplifier binary to a location on the default PATH for all users, avoiding the `/root/.local/bin` accessibility issue when exec runs as a mapped user.

**Additional create parameters**:
```python
"amplifier_bundle": {"type": "string", "description": "Bundle to configure inside the container"},
"amplifier_version": {"type": "string", "description": "Amplifier version to install (default: latest)"},
```

**Add to tool schema** (alongside `as_root` from Task 3.1):
```python
"amplifier_bundle": {"type": "string", "description": "Bundle to configure inside the container"},
"amplifier_version": {"type": "string", "description": "Amplifier version to install (default: latest)"},
```

**Provisioner addition** — `provision_amplifier_settings()`:
```python
async def provision_amplifier_settings(self, container: str, target_home: str) -> ProvisioningStep:
    """Forward Amplifier settings into the container."""
    home = Path.home()
    amplifier_dir = home / ".amplifier"
    if not amplifier_dir.exists():
        return ProvisioningStep("amplifier_settings", "skipped", "No ~/.amplifier directory on host")
    
    # Create target directory
    await self.runtime.run("exec", container, "/bin/sh", "-c",
        f"mkdir -p {target_home}/.amplifier", timeout=5)
    
    files_copied = []
    for settings_file in ["settings.yaml", "settings.local.yaml"]:
        src = amplifier_dir / settings_file
        if src.exists():
            await self.runtime.run("cp", str(src),
                f"{container}:{target_home}/.amplifier/{settings_file}", timeout=10)
            files_copied.append(settings_file)
    
    if not files_copied:
        return ProvisioningStep("amplifier_settings", "skipped", "No settings files found")
    return ProvisioningStep("amplifier_settings", "success", f"Copied {', '.join(files_copied)}")
```

**Tests**:
- `test_amplifier_profile_setup_commands` — verify profile includes uv + amplifier install
- `test_amplifier_profile_forwards_all_creds` — forward_git, forward_gh both true
- `test_amplifier_settings_forwarded` — settings.yaml copied when present
- `test_amplifier_settings_skipped` — skipped when no ~/.amplifier
- `test_amplifier_version_param` — version param modifies install command
- `test_amplifier_bundle_param` — bundle param adds configuration command
- Integration: create amplifier container, run `amplifier --version`, verify output

**Done when**: `containers(create, purpose="amplifier")` produces a container where Amplifier just works, including settings forwarding and credential passthrough.

---

### Task 3.3: Update All Context Docs + README

**What**: Refresh all documentation to reflect Phase 2 and Phase 3 features.

**Files to modify**:
- `context/container-awareness.md` — Thin root context (every session sees this)
- `context/container-guide.md` — Heavy operator agent context
- `agents/container-operator.md` — Operator agent instructions
- `README.md` — User-facing documentation
- `DESIGN.md` — Mark Phase 2 complete, update architecture description
- `docs/PLAN.md` — Fix test counts

**`container-awareness.md` updates**:
- Add provisioning report mention ("create returns structured report — no need to investigate")
- Add background exec operations (exec_background, exec_poll, exec_cancel) to quick reference
- Add cache_clear to operations list
- Add `as_root` parameter mention
- Add `repo_url` parameter mention
- Update patterns section with parallel-agents pattern using exec_background

**`container-guide.md` updates**:
- Add provisioning report section (what each status means, example output)
- Add image caching section (cache_bust, cache_clear, how invalidation works)
- Add try-repo section with detection rules and examples
- Add background exec section (exec_background/poll/cancel lifecycle)
- Add UID/user model section (exec_user, as_root, when to use root)
- Add parallel-agents pattern (create N amplifier containers + exec_background)
- Update troubleshooting section with Phase 2/3 scenarios

**`container-operator.md` updates**:
- Add try-repo to operating principles
- Add background exec pattern for long-running tasks
- Add image cache management guidance
- Add parallel-agents orchestration pattern

**`README.md` updates**:
- Add Phase 2 features to feature list (try-repo, background exec, provisioning report, caching)
- Add `as_root` to exec documentation
- Update operations table

**`DESIGN.md` updates**:
- Phase 2 marked COMPLETE in implementation phases section
- Update section 4.4 (UID mapping) to describe the two-phase user model

**`PLAN.md` updates**:
- Fix test count section (currently says "Total target: ~80 tests", actual is 103+)

**Pre-existing doc bugs to fix**:
- `context/container-guide.md` line ~194: Claims "amplifier settings forwarded if they exist" — this isn't implemented yet (Task 3.2 will add it). Remove or mark as "after Task 3.2".
- `context/container-guide.md` troubleshooting section: References `delete_snapshot` operation which doesn't exist. Change to `snapshot` (for creating) or remove.
- `context/container-awareness.md`: Missing `user` parameter in convenience features table.
- `context/container-guide.md` create parameter reference: Missing `user`, `cache_bust`, `repo_url` parameters.

**Tests**: N/A (documentation)

**Done when**: All docs accurately reflect the current feature set. A developer reading container-awareness.md knows about all available features. README accurately describes the bundle.

---

## Phase 4: Extended Capabilities

> Goal: GPU support, Docker Compose (with informed decision), and further polish.

### Task 4.1: GPU Passthrough — Preflight Detection

**What**: Add an informational GPU runtime check to `_op_preflight`. The `--gpus all` flag is already implemented in `_op_create` (line ~498-500), so this task adds the missing preflight visibility.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — `_op_preflight` method (starts at line ~319)
- `tests/unit/test_tool.py` — Add GPU preflight tests

**Already implemented** (no changes needed):
- `_op_create` adds `--gpus all` to docker run args when `gpu=True` (line ~498-500)
- `gpu` parameter in tool schema (line ~218) and `CreateParams` (line ~55)

**Critical design decision — GPU check must NOT affect `ready`**:
The GPU check is informational only. Many machines don't have GPUs, and `ready=False` would break preflight on every non-GPU machine. The approach: add the GPU check to the `checks` list but always set `passed=True` (since it's optional), with a separate `available` field in the detail to indicate actual GPU status.

**Code to add in `_op_preflight`** — insert after the disk_space check (around line ~415), BEFORE the `all_passed` computation:

```python
# 5. GPU runtime (informational — does not affect ready status)
gpu_available = False
detected_runtime = await self.runtime.detect()
if detected_runtime == "podman":
    checks.append(
        {
            "name": "gpu_runtime",
            "passed": True,  # Always True — GPU is optional
            "detail": "GPU detection not supported for Podman",
            "guidance": None,
        }
    )
else:
    gpu_info = await self.runtime.run(
        "info", "--format", "{{.Runtimes}}", timeout=10
    )
    if gpu_info.returncode == 0 and "nvidia" in gpu_info.stdout.lower():
        gpu_available = True
        checks.append(
            {
                "name": "gpu_runtime",
                "passed": True,
                "detail": "NVIDIA runtime available (GPU passthrough supported)",
                "guidance": None,
            }
        )
    else:
        checks.append(
            {
                "name": "gpu_runtime",
                "passed": True,  # Always True — GPU is optional
                "detail": "NVIDIA runtime not detected (GPU passthrough unavailable)",
                "guidance": "Install nvidia-container-toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html",
            }
        )
```

Note: The check uses the actual 4-field dict shape (`name`, `passed`, `detail`, `guidance`) matching the existing checks — NOT the 6-field shape from the original Task 1.3 spec which was never implemented.

**Note**: This implementation targets Docker + nvidia-container-toolkit. Podman handles GPU passthrough differently (`--device nvidia.com/gpu=all`). Podman GPU support is deferred as a future enhancement.

**Tests** (in `tests/unit/test_tool.py`):

```python
@pytest.mark.asyncio
async def test_gpu_preflight_nvidia_available(tool):
    """GPU check reports nvidia available when runtime has it."""
    # Mock runtime.run: "info --format" returns output containing "nvidia"
    call_count = 0
    async def _mock_run(*args, **kwargs):
        nonlocal call_count
        from amplifier_module_tool_containers.runtime import CommandResult
        call_count += 1
        # First calls are for daemon/permissions checks
        if "info" in args and "--format" in args and "Runtimes" in str(args):
            return CommandResult(returncode=0, stdout="map[io.containerd.runc.v2:{} nvidia:{}]", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")
    tool.runtime.run = _mock_run
    tool.runtime._runtime = "docker"
    
    result = await tool.execute("containers", {"operation": "preflight"})
    gpu_check = next(c for c in result["checks"] if c["name"] == "gpu_runtime")
    assert gpu_check["passed"] is True
    assert "NVIDIA runtime available" in gpu_check["detail"]

@pytest.mark.asyncio
async def test_gpu_preflight_nvidia_unavailable(tool):
    """GPU check reports unavailable but ready is still True."""
    # Mock runtime.run: "info --format" returns output WITHOUT "nvidia"
    async def _mock_run(*args, **kwargs):
        from amplifier_module_tool_containers.runtime import CommandResult
        if "info" in args and "--format" in args and "Runtimes" in str(args):
            return CommandResult(returncode=0, stdout="map[io.containerd.runc.v2:{}]", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")
    tool.runtime.run = _mock_run
    tool.runtime._runtime = "docker"
    
    result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is True  # CRITICAL: GPU absence doesn't break preflight
    gpu_check = next(c for c in result["checks"] if c["name"] == "gpu_runtime")
    assert gpu_check["passed"] is True  # Always True (informational)
    assert "not detected" in gpu_check["detail"]
    assert gpu_check["guidance"] is not None  # Has install guidance

@pytest.mark.asyncio
async def test_gpu_preflight_podman_skipped(tool):
    """GPU check on Podman reports not supported."""
    async def _mock_run(*args, **kwargs):
        from amplifier_module_tool_containers.runtime import CommandResult
        return CommandResult(returncode=0, stdout="", stderr="")
    tool.runtime.run = _mock_run
    tool.runtime._runtime = "podman"
    
    result = await tool.execute("containers", {"operation": "preflight"})
    gpu_check = next(c for c in result["checks"] if c["name"] == "gpu_runtime")
    assert gpu_check["passed"] is True
    assert "not supported for Podman" in gpu_check["detail"]

@pytest.mark.asyncio  
async def test_gpu_check_does_not_affect_ready(tool):
    """ready=True even when GPU is unavailable — GPU is informational only."""
    async def _mock_run(*args, **kwargs):
        from amplifier_module_tool_containers.runtime import CommandResult
        if "info" in args and "--format" in args and "Runtimes" in str(args):
            return CommandResult(returncode=0, stdout="map[io.containerd.runc.v2:{}]", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")
    tool.runtime.run = _mock_run
    tool.runtime._runtime = "docker"
    
    result = await tool.execute("containers", {"operation": "preflight"})
    assert result["ready"] is True

def test_gpu_flag_in_create_schema():
    """Regression guard: gpu=True still produces --gpus all in create args."""
    from amplifier_module_tool_containers import ContainersTool
    tool = ContainersTool()
    schema = tool.tool_definitions[0]["input_schema"]
    assert "gpu" in schema["properties"]
    assert schema["properties"]["gpu"]["type"] == "boolean"
```

**Done when**: `preflight` includes a `gpu_runtime` check that reports NVIDIA availability without affecting `ready` status. All existing + new tests pass. `containers(create, gpu=True)` continues to work unchanged.

---

### Task 4.2: wait_healthy Operation

**What**: Add a `wait_healthy` operation that polls a health-check command inside a container until it succeeds or times out. This closes the startup-ordering gap — the agent can wait for Postgres to accept connections before starting the app.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — Add `_op_wait_healthy` method and add `wait_healthy` to operation enum
- `tests/unit/test_tool.py` — Add unit tests

**Add to operation enum**: `"wait_healthy"`

**Add to input_schema properties**:
```python
"health_command": {
    "type": "string",
    "description": "Command to check service readiness (e.g., 'pg_isready -U postgres')",
},
"interval": {
    "type": "integer",
    "default": 2,
    "description": "Seconds between health check attempts",
},
"retries": {
    "type": "integer",
    "default": 15,
    "description": "Maximum number of health check attempts before timeout",
},
```

**Implementation**:
```python
async def _op_wait_healthy(self, inp: dict[str, Any]) -> dict[str, Any]:
    """Poll a health-check command until it succeeds or retries are exhausted."""
    container = inp.get("container", "")
    health_command = inp.get("health_command", "")
    if not container or not health_command:
        return {"error": "Both 'container' and 'health_command' are required"}

    interval = inp.get("interval", 2)
    retries = inp.get("retries", 15)

    for attempt in range(1, retries + 1):
        result = await self.runtime.run(
            "exec", container, "/bin/sh", "-c", health_command,
            timeout=interval + 5,
        )
        if result.returncode == 0:
            return {
                "healthy": True,
                "container": container,
                "attempts": attempt,
                "detail": f"Health check passed on attempt {attempt}/{retries}",
            }
        if attempt < retries:
            await asyncio.sleep(interval)

    return {
        "healthy": False,
        "container": container,
        "attempts": retries,
        "detail": f"Health check failed after {retries} attempts",
        "last_error": result.stderr.strip() if result else "",
    }
```

Note: This uses `asyncio.sleep` between attempts. `asyncio` is NOT currently imported in `__init__.py` — add `import asyncio` to the imports block at the top of the file (alongside the other stdlib imports like `os`, `json`, `shutil`).

**Usage pattern** (for the agent/docs):
```
containers(create, name="my-db", image="postgres:16",
           env={"POSTGRES_PASSWORD": "dev"}, network="my-stack")
containers(wait_healthy, container="my-db",
           health_command="pg_isready -U postgres",
           interval=2, retries=15)
containers(create, name="my-app", purpose="python", network="my-stack",
           env={"DATABASE_URL": "postgresql://postgres:dev@my-db:5432/app"})
```

**Tests** (in `tests/unit/test_tool.py`):

```python
@pytest.mark.asyncio
async def test_wait_healthy_succeeds_first_attempt(tool):
    """wait_healthy returns healthy=True when check passes immediately."""
    async def _mock_run(*args, **kwargs):
        from amplifier_module_tool_containers.runtime import CommandResult
        return CommandResult(returncode=0, stdout="ready", stderr="")
    tool.runtime.run = _mock_run
    result = await tool.execute("containers", {
        "operation": "wait_healthy",
        "container": "test-db",
        "health_command": "pg_isready",
    })
    assert result["healthy"] is True
    assert result["attempts"] == 1

@pytest.mark.asyncio
async def test_wait_healthy_succeeds_after_retries(tool):
    """wait_healthy returns healthy=True after some failed attempts."""
    call_count = 0
    async def _mock_run(*args, **kwargs):
        nonlocal call_count
        from amplifier_module_tool_containers.runtime import CommandResult
        call_count += 1
        if call_count < 3:
            return CommandResult(returncode=1, stdout="", stderr="not ready")
        return CommandResult(returncode=0, stdout="ready", stderr="")
    tool.runtime.run = _mock_run
    result = await tool.execute("containers", {
        "operation": "wait_healthy",
        "container": "test-db",
        "health_command": "pg_isready",
        "interval": 0,  # No sleep in tests
    })
    assert result["healthy"] is True
    assert result["attempts"] == 3

@pytest.mark.asyncio
async def test_wait_healthy_exhausts_retries(tool):
    """wait_healthy returns healthy=False when all retries fail."""
    async def _mock_run(*args, **kwargs):
        from amplifier_module_tool_containers.runtime import CommandResult
        return CommandResult(returncode=1, stdout="", stderr="connection refused")
    tool.runtime.run = _mock_run
    result = await tool.execute("containers", {
        "operation": "wait_healthy",
        "container": "test-db",
        "health_command": "pg_isready",
        "interval": 0,
        "retries": 3,
    })
    assert result["healthy"] is False
    assert result["attempts"] == 3
    assert "connection refused" in result["last_error"]

@pytest.mark.asyncio
async def test_wait_healthy_requires_params(tool):
    """wait_healthy returns error if container or health_command missing."""
    result = await tool.execute("containers", {
        "operation": "wait_healthy",
        "container": "test-db",
    })
    assert "error" in result

def test_wait_healthy_in_schema():
    """wait_healthy is in the operation enum."""
    from amplifier_module_tool_containers import ContainersTool
    t = ContainersTool()
    schema = t.tool_definitions[0]["input_schema"]
    ops = schema["properties"]["operation"]["enum"]
    assert "wait_healthy" in ops
```

**Done when**: `containers(wait_healthy, container="my-db", health_command="pg_isready")` polls until the health check passes or retries are exhausted. All existing + new tests pass.

---

### Task 4.3: Compose-File Interpretation Pattern (Documentation)

**What**: Add a documented pattern to the context docs showing how the agent should parse a docker-compose.yml and translate it into create + create_network + wait_healthy calls. This is documentation only — no code changes.

**Files to modify**:
- `context/container-guide.md` — Add a new "Interpreting docker-compose.yml" section
- `context/container-awareness.md` — Add wait_healthy to operations table and add brief compose interpretation note
- `agents/container-operator.md` — Add compose interpretation to operating principles

**`container-guide.md` new section** (add after the Multi-Container Patterns section):

```markdown
## Interpreting docker-compose.yml Files

When a user has a docker-compose.yml and wants to use it, DON'T suggest `docker compose up`. Instead, read the file and translate it into container tool calls. This preserves our full provisioning pipeline (credentials, dotfiles, user mapping, tracking).

### Translation Pattern

Given a docker-compose.yml like:
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: dev
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
  redis:
    image: redis:7
  app:
    build: .
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://postgres:dev@db:5432/app
      REDIS_URL: redis://redis:6379
    ports:
      - "8000:8000"
```

Translate to:
```
containers(create_network, name="project-stack")

containers(create, name="db", image="postgres:16",
           env={"POSTGRES_PASSWORD": "dev"},
           ports=[{"host": 5432, "container": 5432}],
           network="project-stack")

containers(wait_healthy, container="db",
           health_command="pg_isready -U postgres",
           interval=2, retries=15)

containers(create, name="redis", image="redis:7",
           network="project-stack")

containers(create, name="app", purpose="python",
           network="project-stack",
           env={"DATABASE_URL": "postgresql://postgres:dev@db:5432/app",
                "REDIS_URL": "redis://redis:6379"},
           ports=[{"host": 8000, "container": 8000}])
```

### Translation Rules

| Compose Feature | Translation |
|----------------|-------------|
| `image:` | `image` parameter on create |
| `environment:` | `env` parameter on create |
| `ports:` | `ports` parameter on create |
| `volumes:` (bind mounts) | `mounts` parameter on create |
| `networks:` | `create_network` + `network` parameter |
| `depends_on: condition: service_healthy` | `wait_healthy` between creates |
| `depends_on:` (basic) | Order your create calls correctly |
| `healthcheck: test:` | `health_command` for wait_healthy |
| `build:` | Not supported — ask user to build image first, then use the image |
| `restart:` | Not applicable — ephemeral containers |
| `env_file:` | Read the .env file, merge into `env` parameter |

### When NOT to translate

If the compose file is very complex (10+ services, custom build contexts, init containers, tmpfs mounts, custom network drivers), tell the user they should use `docker compose` directly via bash. Our tool is for provisioned, managed containers — not for running arbitrary compose stacks.
```

**`container-awareness.md` updates**:
- Add `wait_healthy` to the operations table:
  ```
  | `wait_healthy` | Poll health-check command until service is ready |
  ```
- Add brief note in patterns section about compose interpretation:
  ```
  When a user has a docker-compose.yml, read the YAML and translate services
  into create + create_network + wait_healthy calls. See container-guide.md
  for the full translation pattern.
  ```

**`agents/container-operator.md` updates**:
- Add to operating principles:
  ```
  ### Compose File Interpretation
  When a user has a docker-compose.yml, read the file and translate it into
  tool calls rather than suggesting `docker compose up`. This preserves
  credential forwarding, user mapping, and container tracking. Use
  `wait_healthy` for health-check-based startup ordering. See the
  "Interpreting docker-compose.yml" section in the container guide.
  ```

**Tests**: N/A (documentation only)

**Done when**: All context docs include wait_healthy in operations references, and the compose interpretation pattern is documented for the agent.

---

## Phase 5: Compose Integration + Repos + Config Files

> Goal: Let the LLM write docker-compose.yml (its natural language for multi-service definitions) and enhance it with our provisioning pipeline. Add `repos` for multi-repo cloning and `config_files` for arbitrary file placement.

> **Key insight**: LLMs already know how to write docker-compose.yml perfectly from training data. Fighting that by inventing a custom format wastes instruction tokens. Instead, accept Compose YAML natively and add what Compose can't do (credentials, dotfiles, repos, configs, UID mapping).

### Task 5.1: `compose.py` Module — Compose Lifecycle

**What**: Create a new module that encapsulates Docker Compose CLI operations. This isolates compose logic from the main tool.

**Files to create**:
- `modules/tool-containers/amplifier_module_tool_containers/compose.py`

**Class: `ComposeManager`**:

```python
class ComposeManager:
    """Manages Docker Compose lifecycle via CLI."""

    def __init__(self, runtime: ContainerRuntime):
        self.runtime = runtime

    async def detect_compose(self) -> bool:
        """Check if docker compose (v2 plugin) is available."""
        result = await self.runtime.run("compose", "version", timeout=10)
        return result.returncode == 0

    async def up(
        self,
        compose_file: str,
        project_name: str,
        detach: bool = True,
    ) -> ComposeResult:
        """Run docker compose up."""
        args = ["compose", "-f", compose_file, "-p", project_name, "up"]
        if detach:
            args.append("-d")
        result = await self.runtime.run(*args, timeout=300)
        return ComposeResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def down(self, project_name: str) -> ComposeResult:
        """Run docker compose down for a project."""
        # No -f flag needed — docker compose stores project metadata
        # and can tear down by project name alone
        result = await self.runtime.run(
            "compose", "-p", project_name, "down", "--remove-orphans",
            timeout=120,
        )
        return ComposeResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def ps(self, project_name: str) -> list[dict]:
        """Get status of compose services."""
        result = await self.runtime.run(
            "compose", "-p", project_name, "ps", "--format", "json",
            timeout=10,
        )
        if result.returncode != 0:
            return []
        import json
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

    async def get_network_name(self, project_name: str) -> str | None:
        """Get the default network created by compose for this project."""
        # Compose creates {project}_default network
        network_name = f"{project_name}_default"
        result = await self.runtime.run(
            "network", "inspect", network_name, timeout=10
        )
        if result.returncode == 0:
            return network_name
        return None
```

**`ComposeResult` dataclass**:
```python
@dataclass
class ComposeResult:
    success: bool
    stdout: str
    stderr: str
```

**Tests** (in `tests/unit/test_compose.py` — new file):
- `test_detect_compose_available` — mock runtime returns success for `compose version`
- `test_detect_compose_unavailable` — mock runtime returns failure
- `test_up_builds_correct_args` — verify args include -f, -p, -d flags
- `test_down_builds_correct_args` — verify args include -p and --remove-orphans
- `test_ps_parses_json` — mock runtime returns JSON, verify parsing
- `test_ps_handles_failure` — returns empty list on failure
- `test_get_network_name` — returns `{project}_default` when network exists

**Done when**: `ComposeManager` can up/down/ps/detect compose and get the network name. All tests pass.

---

### Task 5.2: `repos` Parameter — Multi-Repo Cloning

**What**: Add a `repos` parameter to `create` that clones multiple repos into the container with optional install commands. Results appear in the provisioning report.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — Add repos handling to `_op_create`
- `modules/tool-containers/amplifier_module_tool_containers/provisioner.py` — Add `provision_repos` method

**Add to provisioner.py**:
```python
async def provision_repos(
    self,
    container: str,
    repos: list[dict[str, str]],
) -> ProvisioningStep:
    """Clone repos into the container and optionally run install commands."""
    if not repos:
        return ProvisioningStep("repos", "skipped", "No repos specified")

    cloned = []
    failed = []
    for repo in repos:
        url = repo.get("url", "")
        path = repo.get("path", f"/workspace/{url.rstrip('/').split('/')[-1]}")
        install = repo.get("install")

        # Clone
        clone_result = await self.runtime.run(
            "exec", container, "/bin/sh", "-c",
            f"git clone {url} {path}",
            timeout=120,
        )
        if clone_result.returncode != 0:
            failed.append({"url": url, "error": clone_result.stderr.strip()})
            continue

        # Install (optional, runs as root since it's a setup operation)
        if install:
            install_result = await self.runtime.run(
                "exec", container, "/bin/sh", "-c",
                f"cd {path} && {install}",
                timeout=300,
            )
            if install_result.returncode != 0:
                failed.append({"url": url, "error": f"Install failed: {install_result.stderr.strip()}"})
                continue

        cloned.append(url.split("/")[-1])

    if failed and not cloned:
        return ProvisioningStep(
            "repos", "failed",
            f"All {len(failed)} repos failed to clone",
            error=str(failed),
        )
    elif failed:
        return ProvisioningStep(
            "repos", "partial",
            f"{len(cloned)}/{len(cloned) + len(failed)} repos cloned",
            error=str(failed),
        )
    return ProvisioningStep(
        "repos", "success",
        f"Cloned {len(cloned)} repos: {', '.join(cloned)}",
    )
```

**Add to `_op_create`** — after dotfiles provisioning, before setup_commands:
```python
# Clone repos
repos_list = inp.get("repos", [])
if repos_list:
    report.append(await self.provisioner.provision_repos(name, repos_list))
else:
    report.append(ProvisioningStep("repos", "skipped", "No repos specified"))
```

**Add to tool schema**:
```python
"repos": {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Git URL to clone"},
            "path": {"type": "string", "description": "Clone destination (default: /workspace/{repo-name})"},
            "install": {"type": "string", "description": "Optional install command (e.g., 'pip install -e .')"},
        },
        "required": ["url"],
    },
    "description": "Repos to clone into the container",
},
```

**Tests** (in `tests/unit/test_provisioner.py`):
- `test_provision_repos_success` — 2 repos cloned, both succeed
- `test_provision_repos_with_install` — repo cloned + install command runs
- `test_provision_repos_clone_failure` — one repo fails, reports partial
- `test_provision_repos_all_fail` — all fail, reports failed
- `test_provision_repos_empty` — empty list returns skipped
- `test_provision_repos_default_path` — url without explicit path gets /workspace/{name}

**Done when**: `repos=[{url, path, install}]` on create clones repos and shows results in provisioning report.

---

### Task 5.3: `config_files` Parameter — Arbitrary File Placement

**What**: Add a `config_files` parameter to `create` that writes files to arbitrary paths inside the container (not just `~/` like dotfiles_inline).

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — Add config_files handling to `_op_create`
- `modules/tool-containers/amplifier_module_tool_containers/provisioner.py` — Add `provision_config_files` method

**Add to provisioner.py**:
```python
async def provision_config_files(
    self,
    container: str,
    config_files: dict[str, str],
) -> ProvisioningStep:
    """Write config files to arbitrary paths inside the container."""
    if not config_files:
        return ProvisioningStep("config_files", "skipped", "No config files specified")

    written = []
    failed = []
    for path, content in config_files.items():
        escaped = content.replace("'", "'\\''")
        result = await self.runtime.run(
            "exec", container, "/bin/sh", "-c",
            f"mkdir -p $(dirname '{path}') && cat > '{path}' << 'AMPLIFIER_CONFIG_EOF'\n{escaped}\nAMPLIFIER_CONFIG_EOF",
            timeout=10,
        )
        if result.returncode == 0:
            written.append(path)
        else:
            failed.append({"path": path, "error": result.stderr.strip()})

    if failed and not written:
        return ProvisioningStep(
            "config_files", "failed",
            f"All {len(failed)} files failed",
            error=str(failed),
        )
    elif failed:
        return ProvisioningStep(
            "config_files", "partial",
            f"{len(written)}/{len(written) + len(failed)} files written",
            error=str(failed),
        )
    return ProvisioningStep(
        "config_files", "success",
        f"Wrote {len(written)} files: {', '.join(written)}",
    )
```

**Add to `_op_create`** — after repos provisioning, before setup_commands:
```python
# Write config files
config_files_dict = inp.get("config_files", {})
if config_files_dict:
    report.append(await self.provisioner.provision_config_files(name, config_files_dict))
else:
    report.append(ProvisioningStep("config_files", "skipped", "No config files specified"))
```

**Add to tool schema**:
```python
"config_files": {
    "type": "object",
    "description": "Files to write: {'/path/in/container': 'file content'}",
},
```

**Tests** (in `tests/unit/test_provisioner.py`):
- `test_provision_config_files_success` — 2 files written, both succeed
- `test_provision_config_files_creates_dirs` — parent directories created automatically
- `test_provision_config_files_failure` — one file fails, reports partial
- `test_provision_config_files_empty` — empty dict returns skipped

**Done when**: `config_files={"/path": "content"}` writes files and shows results in provisioning report.

---

### Task 5.4: Compose Integration in `_op_create` and `_op_destroy`

**What**: Wire compose into the create/destroy lifecycle. When `compose_content` or `compose_file` is provided, run compose up before creating the primary container, join the compose network, and tear down compose on destroy.

**Files to modify**:
- `modules/tool-containers/amplifier_module_tool_containers/__init__.py` — Modify `_op_create`, `_op_destroy`, `_op_status`
- `modules/tool-containers/amplifier_module_tool_containers/compose.py` (from Task 5.1)

**Changes to `_op_create`**:

At the top of `_op_create`, BEFORE building docker run args:

```python
# Handle compose
compose_content = inp.get("compose_content")
compose_file_path = inp.get("compose_file")

    # Mutual exclusion
    if compose_content and compose_file_path:
        return {"error": "Provide compose_content OR compose_file, not both"}

compose_project = None
compose_network = None

if compose_content or compose_file_path:
    from .compose import ComposeManager
    compose_mgr = ComposeManager(self.runtime)

    # Check compose is available
    if not await compose_mgr.detect_compose():
        return {"error": "docker compose not available. Install the compose plugin."}

    compose_project = name  # Use container name as compose project name

    if compose_content:
        # Write compose content to a temp file on the HOST
        import tempfile
        compose_tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", prefix=f"amp-compose-{name}-", delete=False
        )
        compose_tmp.write(compose_content)
        compose_tmp.close()
        compose_file_path = compose_tmp.name

    # Run compose up
    compose_result = await compose_mgr.up(compose_file_path, compose_project)
    if not compose_result.success:
        return {
            "error": f"docker compose up failed: {compose_result.stderr.strip()}",
        }

    # Get the compose network so our primary container can join it
    compose_network = await compose_mgr.get_network_name(compose_project)
```

Then when building docker run args, if `compose_network` is set, use it as the network:
```python
# If compose created a network, join it instead of bridge
if compose_network:
    network = compose_network
```

Store compose metadata:
```python
# In metadata save:
metadata["compose_project"] = compose_project
metadata["compose_file"] = compose_file_path  # For compose down later
metadata["compose_network"] = compose_network
```

Add compose services to provisioning report:
```python
if compose_project:
    compose_services = await compose_mgr.ps(compose_project)
    service_names = [s.get("Service", s.get("Name", "?")) for s in compose_services]
    report.append(ProvisioningStep(
        "compose", "success",
        f"Started {len(compose_services)} services: {', '.join(service_names)}",
    ))
```

**Changes to `_op_destroy`**:

Before destroying the primary container, check for compose and tear it down:
```python
# Check if this container has compose services
metadata = self.store.load(container)
compose_project = (metadata or {}).get("compose_project")
if compose_project:
    from .compose import ComposeManager
    compose_mgr = ComposeManager(self.runtime)
    await compose_mgr.down(compose_project)
    # Clean up temp compose file if it exists
    compose_file = (metadata or {}).get("compose_file")
    if compose_file and compose_file.startswith("/tmp/amp-compose-"):
        import os
        try:
            os.unlink(compose_file)
        except OSError:
            pass
```

**Changes to `_op_status`**:

Include compose service status when available:
```python
# In _op_status, after getting container status:
compose_project = (metadata or {}).get("compose_project")
if compose_project:
    from .compose import ComposeManager
    compose_mgr = ComposeManager(self.runtime)
    compose_services = await compose_mgr.ps(compose_project)
    status_result["compose_services"] = compose_services
```

**Add to tool schema**:
```python
"compose_content": {
    "type": "string",
    "description": "Docker Compose YAML content (the LLM writes this naturally)",
},
"compose_file": {
    "type": "string",
    "description": "Path to existing docker-compose.yml on the host",
},
```

**Tests** (in `tests/unit/test_tool.py`):
- `test_create_with_compose_content` — compose_content triggers compose up, primary container joins compose network
- `test_create_compose_unavailable` — returns error when docker compose not installed
- `test_create_compose_up_failure` — returns error when compose up fails
- `test_destroy_with_compose` — destroy runs compose down before removing primary container
- `test_status_includes_compose_services` — status shows compose services when present
- `test_create_compose_content_and_file_error` — returns error when both compose_content and compose_file provided
- `test_create_compose_in_provisioning_report` — report includes compose step with service names

**Integration tests** (in `tests/integration/test_phase5.py`):
- `test_compose_postgres_with_primary` — create with compose_content (postgres), primary container can connect via network
- `test_compose_destroy_cleans_up` — destroy removes both primary and compose services
- `test_repos_cloned_in_container` — create with repos, verify repos cloned inside container
- `test_config_files_written` — create with config_files, verify files exist at correct paths

**Done when**: `compose_content` on create starts compose services, primary container joins the network. Destroy tears down everything. Status shows compose services. Full provisioning report covers compose + repos + config_files.

---

### Task 5.5: Update Documentation

**What**: Update all context docs to cover Phase 5 features.

**Files to modify**:
- `context/container-awareness.md` — Add compose_content, repos, config_files to convenience features and quick reference
- `context/container-guide.md` — Update the "Interpreting docker-compose.yml" section to show the new native approach (compose_content parameter instead of manual translation). Keep the translation table as reference. Add repos and config_files documentation.
- `agents/container-operator.md` — Update compose interpretation principle to use compose_content natively
- `README.md` — Add Phase 5 features
- `DESIGN.md` — Mark Phase 5 complete (after implementation)

**Key doc change**: The container-guide's "Interpreting docker-compose.yml" section currently teaches manual translation (read YAML, issue N create calls). This should be updated to show the native approach first:

```markdown
## Using docker-compose.yml

### Native Compose Support (Preferred)

Pass compose YAML directly — the tool handles the rest:

    containers(create, name="my-stack",
        compose_content="""
    services:
      db:
        image: postgres:16
        environment:
          POSTGRES_PASSWORD: dev
      redis:
        image: redis:7
    """,
        purpose="python",
        repos=[...],
        config_files={...},
        forward_gh=True,
    )

The tool runs `docker compose up` for infrastructure services, creates a fully-provisioned primary container on the same network, and returns a complete provisioning report.

### Manual Translation (For Complex Cases)

For compose files that need our provisioning on multiple services, or when you want fine-grained control, you can still translate manually...
```

**Tests**: N/A (documentation only)

**Done when**: All docs cover compose_content, repos, config_files. The compose interpretation docs show native approach first.

---

## Testing Strategy

### Test Categories

| Category | Location | Requires Docker | CI |
|----------|----------|----------------|-----|
| Unit tests | `tests/unit/` | No | Yes |
| Integration tests | `tests/integration/` | Yes | Needs Docker-in-Docker |

### Running Tests

```bash
# All tests
pytest tests/ -v

# Unit tests only (fast, no Docker needed)
pytest tests/unit/ -v

# Integration tests only (needs Docker)
pytest tests/integration/ -v

# Skip integration tests
pytest tests/ -v -m "not integration"
```

### Current Test Count
- Phase 1: 43 unit + 11 integration = 54 tests
- Phase 2: +49 tests = 103 total (87 unit + 16 integration)
- Phase 3: +20 tests = 123 total (107 unit + 16 integration)
- Phase 4: +10 tests = 133 total (117 unit + 16 integration)
- Phase 5 target: ~25 additional tests (unit + integration)
- Total target: ~158 tests
