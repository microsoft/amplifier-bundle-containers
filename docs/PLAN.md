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

**What**: Add GPU runtime detection to `preflight`. The `--gpus all` flag is already implemented in `_op_create` (added when `gpu=True`), so this task focuses on the missing preflight piece.

**Already implemented** (no changes needed):
- `_op_create` already adds `--gpus all` to docker run args when `gpu=True` is passed

**Changes needed**:
- Add a GPU-specific preflight check to `_op_preflight` that detects whether the NVIDIA container runtime is available via `docker info --format '{{.Runtimes}}'` (or equivalent)
- The check should report whether GPU passthrough is available, not fail if it isn't (GPU is optional)
- Include guidance when GPU is requested but runtime is not available (install `nvidia-container-toolkit`)

**Tests**:
- `test_gpu_preflight_check_available` — nvidia runtime detected, reports available
- `test_gpu_preflight_check_unavailable` — no nvidia runtime, reports unavailable with guidance
- `test_gpu_flag_already_in_create` — verify existing `--gpus all` in docker run args (regression guard)
- Integration (GPU hosts only): create GPU container, run `nvidia-smi`

**Done when**: `preflight` reports GPU availability. `containers(create, gpu=True)` continues to work on hosts with NVIDIA Docker runtime.

---

### Task 4.2: Docker Compose Evaluation and Implementation

**What**: Evaluate whether to add Docker Compose pass-through, then implement if warranted.

**Before implementation**, prepare a pro/con brief for the user:

**What Compose gives you**:
- Declarative multi-service definition in a single YAML file
- Automatic network creation between services
- Volume management and data persistence
- Health checks and dependency ordering (service A waits for service B)
- One-command teardown of entire stacks
- Widely adopted format — many projects ship a docker-compose.yml

**What you lose / trade off**:
- Another YAML format to understand (vs our `create` + `create_network` approach)
- Compose files may conflict with our container tracking (labels, metadata)
- Users need `docker compose` plugin installed (additional prerequisite)
- Less granular control than individual `create` calls
- Our provisioning pipeline (env forwarding, dotfiles, etc.) doesn't apply to compose services

**Recommendation**: Compose is most useful when users already have a docker-compose.yml they want to use. Our create+network approach is better for agent-driven orchestration. Both can coexist.

**If approved**, implement:
- `compose_up(compose_file, project_name)` — `docker compose -f file up -d`
- `compose_down(project_name)` — `docker compose down`
- `compose_status(project_name)` — `docker compose ps --format json`

**Tests**: Standard lifecycle tests with a simple compose file (nginx + redis).

**Done when**: User has made an informed decision. If approved, compose operations work.

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
- Phase 3 target: ~15 additional tests
- Total target: ~118 tests
