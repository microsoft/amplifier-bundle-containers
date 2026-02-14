# amplifier-bundle-containers - Design Document

> **Status**: Revised after Phase 1 implementation
> **Date**: 2025-02-12

---

## 1. Vision

**One sentence**: The assistant can spin up, configure, interact with, and hand off isolated container environments for *any* purpose, with sensible defaults that eliminate the usual friction of "set up Docker, figure out mounts, forward credentials, connect" — so the user just says what they want and it works.

**The problem today**: When a user asks Amplifier "can you set me up a container so I can try out this sketchy repo?" — the assistant has to improvise a long chain of bash commands, user guidance for Docker setup, manual credential forwarding, etc. Every time. With no memory of what worked. And the user has to do a dozen manual steps.

**The solution**: A behavior bundle that:
1. Verifies the host is ready (or guides setup where it can't auto-fix)
2. Creates containers with a single tool call, pre-configured with the user's identity and credentials
3. Lets the agent puppet the container for setup work, then hands it off to the user with a simple `docker exec -it` command
4. Manages the full lifecycle: create, configure, use, snapshot, destroy
5. Handles multi-container scenarios for parallel isolated workloads

---

## 2. Use Cases

### Tier 1: Core (must nail)
- **Safe repo exploration** — "Try out this GitHub repo without risking my host"
- **Parallel isolated workloads** — N containers each running Amplifier on different tasks
- **Clean development environment** — "Set me up a fresh Python/Node/Rust environment"
- **Destructive experimentation** — "I want to try installing a bunch of stuff"

### Tier 2: Important
- **Service prototyping** — "Spin up Postgres + Redis + my app"
- **Build/CI simulation** — "Test if this builds cleanly on a fresh system"
- **Learning environment** — "Set up a container where I can learn Rust"
- **Cross-platform testing** — "Does this work on Ubuntu 24.04?"

### Tier 3: Extensibility
- GPU workloads, network simulation, long-running services, team environments

---

## 3. The Convenience Gap

| # | Manual Step Today | With This Bundle |
|---|-------------------|-----------------|
| 1 | Is Docker/Podman installed? | `preflight` auto-detects, guides if missing |
| 2 | Is the daemon running? | `preflight` checks, offers fix commands |
| 3 | Does user have permissions? | `preflight` checks, offers guidance |
| 4 | What base image to use? | Smart defaults per language/purpose |
| 5 | `docker run` with what flags? | Single `create` operation, sane defaults |
| 6 | How to mount the project? | Auto-mounts CWD or specified paths |
| 7 | Forward env vars for API keys? | Auto-passthrough with configurable patterns |
| 8 | Forward git credentials? | Auto-copies `.gitconfig`, credential helpers |
| 9 | Forward GH CLI auth? | Maps `gh auth token` into container |
| 10 | Forward SSH keys? | Opt-in bind-mount of `~/.ssh` read-only |
| 11 | Shell prompt/preferences? | Dotfiles repo or comfort settings |
| 12 | How does user connect? | Gives them the exact command |
| 13 | What about cleanup? | Tracks containers, offers destroy |
| 14 | What if they want to save state? | Snapshot/commit support |

Steps 1-11 happen in a single `create` call with smart defaults.

---

## 4. Architecture

### 4.1 Bundle Structure

```
amplifier-bundle-containers/
├── bundle.md                              # Thin root: includes foundation + behavior
├── behaviors/
│   ├── containers.yaml                    # Core: tool + agent + context
│   └── container-safety.yaml              # Optional: approval hooks
├── agents/
│   └── container-operator.md              # Specialist for complex work
├── context/
│   ├── container-awareness.md             # Thin context for root session
│   └── container-guide.md                 # Heavy context for operator agent
├── modules/
│   ├── tool-containers/                   # Core tool module
│   │   ├── pyproject.toml
│   │   └── amplifier_module_tool_containers/
│   │       ├── __init__.py                # Tool class, lifecycle, provisioning + mount()
│   │       ├── runtime.py                 # Docker/Podman detection
│   │       ├── provisioner.py             # Env var matching and passthrough
│   │       └── images.py                  # Purpose profiles and image selection
│   └── hooks-container-safety/            # Optional safety hooks
│       ├── pyproject.toml
│       └── amplifier_module_hooks_container_safety/
│           └── __init__.py
├── images/                                # Optional curated Dockerfiles
│   └── amplifier-base/
│       └── Dockerfile
├── docs/
│   └── PLAN.md
├── README.md
└── LICENSE
```

### 4.2 Tool at Root + Specialist Agent

Unlike shadow (agent-scoped tool only), container management is available at root level:
- Users say "spin me up a container" directly — no delegation overhead for simple cases
- `container-operator` agent handles complex multi-container orchestration
- Root session gets thin `container-awareness.md`; operator gets heavy `container-guide.md`

### 4.3 Composition Model

**App-level behavior** (recommended):
```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-containers@main --app
```

**Included in another bundle**:
```yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-containers@main#subdirectory=behaviors/containers.yaml
```

---

### 4.4 Two-Phase User Model

Containers run as root for full admin capability during setup. A user matching the host UID/GID is created inside the container. After setup, exec commands run as the mapped user by default for correct file ownership on mounted volumes.

```
docker run ... (root)                           ← Setup phase: apt-get, pip install work
docker exec --user UID:GID container command    ← Exec phase: files owned by host user
docker exec container command                   ← Admin: as_root=True, full root access
```

This design preserves `--security-opt=no-new-privileges` (sudo not needed — the Docker daemon handles user switching via `docker exec --user`). A `user` parameter on `create` allows override, and `as_root=True` on `exec` gives admin access at any time.

## 5. Tool Operations API

### Core Lifecycle
| Operation | Description |
|-----------|-------------|
| `preflight` | Check Docker/Podman, daemon, permissions, disk space |
| `create` | Create fully provisioned container |
| `exec` | Execute command inside container |
| `exec_interactive_hint` | Return shell command for user to connect |
| `list` | All managed containers |
| `status` | Detailed status with optional health check |
| `destroy` | Stop and remove container |
| `destroy_all` | Remove all managed containers |
| `create_network` | Docker network for service stacks |
| `destroy_network` | Remove network |

### File Transfer
| Operation | Description |
|-----------|-------------|
| `copy_in` | Host to container |
| `copy_out` | Container to host |

### State Management
| Operation | Description |
|-----------|-------------|
| `snapshot` | Commit container state as named image |
| `restore` | Create container from snapshot |

### Multi-Container (Phase 4)
| Operation | Description |
|-----------|-------------|
| `compose_up` | docker-compose pass-through |
| `compose_down` | Tear down compose services |

---

## 6. Create Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | auto | Human-friendly container name |
| `image` | string | from config | Base image |
| `purpose` | string | none | Smart defaults hint |
| `workdir` | string | "/workspace" | Working directory |
| `mounts` | list | [] | Host bind-mounts |
| `mount_cwd` | bool | true | Auto-mount CWD to /workspace |
| `ports` | list | [] | Port mappings |
| `env` | dict | {} | Additional env vars |
| `env_passthrough` | string | "auto" | "auto", "all", "none", or list |
| `forward_git` | bool | config | Copy .gitconfig |
| `forward_gh` | bool | config | Forward GH CLI auth |
| `forward_ssh` | bool | config | Mount ~/.ssh read-only |
| `dotfiles_repo` | string | config | Dotfiles git URL |
| `dotfiles_script` | string | auto-detect | Install script to run |
| `dotfiles_branch` | string | default | Branch to clone |
| `dotfiles_target` | string | ~/.dotfiles | Clone destination |
| `dotfiles_inline` | dict | none | Inline file content |
| `dotfiles_skip` | bool | false | Skip dotfiles |
| `setup_commands` | list | [] | Post-provisioning commands |
| `memory_limit` | string | "4g" | Memory limit |
| `cpu_limit` | float | none | CPU cores |
| `gpu` | bool | false | GPU passthrough |
| `network` | string | "bridge" | Network mode |
| `persistent` | bool | false | Survive session end |

---

## 7. Provisioning Pipeline

```
Container created and started
    |
    +-- 1. Provision environment variables (env_passthrough)
    +-- 2. Forward git config (forward_git)
    +-- 3. Forward GH auth (forward_gh)    <- needed for private dotfiles
    +-- 4. Forward SSH keys (forward_ssh)
    +-- 5. Clone and run dotfiles          <- runs AFTER credentials available
    +-- 6. Apply purpose profile setup     <- language-specific tooling
    +-- 7. Run setup_commands              <- user's additional setup
    +-- 8. Container ready
```

Order matters: GH auth before dotfiles (for private repos). Dotfiles before setup_commands (user can override).

### 7.1 Provisioning Report

Every `create` call returns a structured `provisioning_report` alongside the container info. This eliminates the need for the caller to investigate what happened:

```json
{
    "success": true,
    "container": "amp-python-a3f2",
    "provisioning_report": {
        "env_passthrough": {"status": "success", "vars_injected": 5},
        "forward_git": {"status": "success", "detail": "Copied .gitconfig, .gitconfig.local"},
        "forward_gh": {"status": "success", "detail": "GH token injected, gh auth login completed"},
        "forward_ssh": {"status": "skipped", "detail": "Not requested"},
        "dotfiles": {"status": "success", "detail": "Cloned user/dotfiles, ran install.sh"},
        "purpose_setup": {"status": "success", "detail": "Installed uv, created venv"},
        "setup_commands": {"status": "partial", "detail": "2/3 commands succeeded", "failures": ["apt install foo: package not found"]}
    }
}
```

Each provisioning step reports: `success`, `skipped`, `failed`, or `partial`. The caller gets full visibility without needing to exec into the container to check.

---

## 8. Three-Layer Provisioning Model

```
Layer 3: Dotfiles repo (comprehensive, user-maintained)
Layer 2: Individual forwarding (forward_git, forward_gh, etc.)
Layer 1: Explicit env/config (env={...}, setup_commands)
```

Each layer builds on the one below. Dotfiles are additive to individual forwarding, not a replacement. Users without a dotfiles repo still get excellent convenience via Layer 2.

### Dotfiles Install Script Resolution
1. Explicit `dotfiles_script` parameter
2. `install.sh`
3. `setup.sh`
4. `bootstrap.sh`
5. `script/setup`
6. `Makefile` (run `make`)
7. Smart symlink of common dotfiles (fallback)

### 8.1 Local Image Caching

Purpose profiles build on stock Docker images but install packages at creation time (slow). To accelerate repeated use:

1. After first successful creation with a purpose profile, `docker commit` the provisioned state as `amplifier-cache:{purpose}` (e.g., `amplifier-cache:python`)
2. On subsequent creates with the same purpose, check if cached image exists and use it
3. Cache is local-only — no registry publishing required
4. Cache can be invalidated manually or via a `cache_bust=true` parameter

This avoids the 30-60 second `apt-get install` on every container creation while staying fully local.

---

## 9. Purpose Profiles

| Purpose | Image | Setup |
|---------|-------|-------|
| `"python"` | python:3.12-slim | uv, venv |
| `"node"` | node:20-slim | corepack |
| `"rust"` | rust:1-slim | build-essential, pkg-config, libssl-dev |
| `"go"` | golang:1.22 | go toolchain |
| `"general"` | ubuntu:24.04 | build-essential, git, curl, jq, tree, vim-tiny |
| `"amplifier"` | python:3.12-slim | uv tool install amplifier, auto-forward creds |
| `"try-repo"` | dynamic | Inspect repo, select profile, clone + setup |
| `"clean"` | ubuntu:24.04 | NO dotfiles, NO forwarding -- pristine |

---

## 10. Security Defaults

Container hardening:
```
--security-opt=no-new-privileges
--memory=4g
--pids-limit=256
--network=bridge
```

Docker's default capability set is used (not `--cap-drop=ALL`) to allow package installation, user creation, and file ownership changes inside the container. The primary security control is `no-new-privileges`, which prevents setuid privilege escalation.

Never privileged by default. Never host network by default.

| Forwarded Item | Default | Risk |
|----------------|---------|------|
| API keys (patterns) | Auto | Medium |
| Git config | On | Low |
| GH CLI token | On | Medium |
| SSH keys | **Off** | High |
| All env vars | **Off** | High |

---

## 11. Container Tracking

Labels: `amplifier.managed`, `amplifier.bundle`, `amplifier.session`, `amplifier.purpose`, `amplifier.persistent`.

Metadata in `~/.amplifier/containers/registry.json`.

Non-persistent containers cleaned up on `session:end` via hook.

---

## 12. Interaction Modes

1. **Agent Puppet** — Agent creates and works inside the container
2. **User Handoff** — Agent sets up, gives user the connect command
3. **Hybrid** — Agent sets up, user works, agent assists further
4. **Amplifier-in-Container** — Running Amplifier itself inside containers for parallel agents

---

## 13. Relationship to Shadow Bundle

Independent. Shadow's value is Gitea + git URL rewriting + source snapshotting. This bundle is general-purpose. Future: extract shared container-runtime utility.

---

## 14. Implementation Phases

### Phase 1: Core MVP — COMPLETE
Runtime detection, preflight, create/exec/destroy, env passthrough, git/GH/SSH forwarding, dotfiles, purpose profiles, CWD mount, tracking, context docs, snapshots, networks. 54 tests (43 unit + 11 integration).

### Phase 2: Production Readiness — COMPLETE
UID/GID mapping, provisioning report, local image caching, try-repo auto-detection, background exec with polling, dev infrastructure. 103 tests (87 unit + 16 integration).

### Phase 3: Two-Phase User Model + Amplifier-in-Container
Refactor to two-phase user model (root setup, mapped user exec), amplifier purpose profile polish (settings forwarding, parallel-agents pattern), documentation refresh.

### Phase 4: Extended Capabilities
GPU passthrough, Docker Compose pass-through (with pro/con evaluation), curated image publishing (when ready).
