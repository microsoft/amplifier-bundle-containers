# amplifier-bundle-containers

General-purpose container management for Amplifier agents. Spin up isolated Docker/Podman environments with a single tool call — complete with credential forwarding, smart language defaults, dotfiles integration, and user handoff.

## What This Solves

When a user asks Amplifier "set me up a container to try out this repo," the assistant today has to improvise Docker commands, manually forward credentials, guide the user through setup steps, and hope it all works. This bundle collapses that 14-step process into one or two tool calls with smart defaults.

## Quick Start

### Add to your Amplifier setup

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-containers@main --app
```

### Or include in a bundle

```yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-containers@main
```

### Or include just the behavior

```yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-containers@main#subdirectory=behaviors/containers.yaml
```

## What You Can Do

**Ask your assistant things like:**

- "Set me up a Python container to try out this repo"
- "Create an isolated environment for this project"
- "Spin up three containers, each running a different task"
- "Set up Postgres + Redis + my app in containers"
- "Give me a clean Rust environment to experiment in"
- "Try out https://github.com/org/cool-project for me"

**The assistant will:**

1. Check that Docker/Podman is available (guide you through setup if not)
2. Create a container with the right base image and tools
3. Forward your git config, GH CLI auth, and API keys automatically
4. Apply your dotfiles if configured
5. Give you the exact command to connect

## Features

### Smart Defaults via `purpose`

| Purpose | What You Get |
|---------|-------------|
| `python` | Python 3.12 + uv + venv |
| `node` | Node 20 + corepack (yarn/pnpm) |
| `rust` | Rust toolchain + build tools |
| `go` | Go 1.22 toolchain |
| `general` | Ubuntu 24.04 + common dev tools |
| `amplifier` | Amplifier pre-installed + all credentials + settings forwarded |
| `try-repo` | Auto-detect language from repo, clone and set up |
| `clean` | Pristine — no dotfiles, no forwarding |

### Automatic Credential Forwarding

| What | Default | Description |
|------|---------|-------------|
| API keys | Auto | Forwards vars matching `*_API_KEY`, `ANTHROPIC_*`, `OPENAI_*`, etc. |
| Git config | On | Copies `.gitconfig` so git works naturally |
| GH CLI auth | On | Forwards `gh auth token` for private repo access |
| SSH keys | Off | Opt-in bind-mount of `~/.ssh` (read-only) |

### Dotfiles Integration

Bring your own dotfiles repo for a container that feels like home:

```
containers(operation="create",
    purpose="python",
    dotfiles_repo="https://github.com/you/dotfiles")
```

Supports install scripts (`install.sh`, `setup.sh`, `bootstrap.sh`, `Makefile`) or auto-symlinks common dotfiles as fallback.

### Two-Phase User Model

Containers run as root during setup (package installation, user creation) then switch to a mapped user matching your host UID:GID for all `exec` commands. This ensures files created on mounted volumes have correct ownership. Use `as_root=True` on `exec` for admin operations at any time.

### Try-Repo Auto-Detection

Point the tool at any git repo and it auto-detects the language, selects the right base image, clones the repo, and runs setup:

```
containers(operation="create", purpose="try-repo",
    repo_url="https://github.com/org/cool-project")
```

### Provisioning Report

Every `create` returns a structured report showing the status of each provisioning step (env vars, git config, GH auth, dotfiles, setup commands). No need to investigate — the report tells you exactly what succeeded, failed, or was skipped.

### Image Caching

Purpose-based images are cached locally after first creation. Second and subsequent creates with the same purpose skip package installation entirely. Use `cache_bust=True` for a fresh build or `cache_clear` to remove cached images.

### Background Execution

Run long-running tasks without blocking:

```
containers(operation="exec_background", container="my-env",
    command="pytest -v --slow")
# Returns immediately with job_id

containers(operation="exec_poll", container="my-env", job_id="a3f2e1c8")
# Check progress, get last 100 lines of output
```

### Amplifier-in-Container

Run parallel Amplifier agents, each in its own isolated container with full credentials and settings forwarded:

```
containers(operation="create", name="agent-a", purpose="amplifier",
    amplifier_bundle="git+https://github.com/org/bundle@main")
containers(operation="exec_background", container="agent-a",
    command="amplifier run 'refactor the auth module'")
```

### Operations

**Core Lifecycle**

| Operation | Description |
|-----------|-------------|
| `preflight` | Check Docker/Podman readiness |
| `create` | Create a provisioned container |
| `exec` | Run commands inside a container |
| `exec_interactive_hint` | Get the connect command for the user |
| `list` / `status` | See what's running |
| `destroy` / `destroy_all` | Clean up |

**File Transfer**

| Operation | Description |
|-----------|-------------|
| `copy_in` / `copy_out` | Transfer files between host and container |

**State Management**

| Operation | Description |
|-----------|-------------|
| `snapshot` / `restore` | Save and restore container state |

**Networks**

| Operation | Description |
|-----------|-------------|
| `create_network` / `destroy_network` | Docker networks for service stacks |

**Cache**

| Operation | Description |
|-----------|-------------|
| `cache_clear` | Remove cached purpose images |

**Background Execution**

| Operation | Description |
|-----------|-------------|
| `exec_background` | Start a long-running command, get a job_id |
| `exec_poll` | Check status and output of a background job |
| `exec_cancel` | Kill a background job |

### Multi-Container Support

Create Docker networks for service stacks where containers communicate by name:

```
containers(operation="create_network", name="my-stack")
containers(operation="create", name="my-db", image="postgres:16", network="my-stack")
containers(operation="create", name="my-app", purpose="python", network="my-stack")
```

### Optional Safety Policies

Include the safety behavior for approval gates on dangerous operations:

```yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-bundle-containers@main#subdirectory=behaviors/container-safety.yaml
```

Adds approval prompts for: GPU access, host networking, sensitive path mounts, SSH forwarding, and destroy-all.

## Configuration

Default behavior can be customized via settings:

```yaml
# ~/.amplifier/settings.yaml
modules:
  tools:
    - module: tool-containers
      config:
        default_image: "ubuntu:24.04"
        max_containers: 10
        auto_passthrough:
          env_patterns:
            - "*_API_KEY"
            - "ANTHROPIC_*"
            - "OPENAI_*"
          forward_git: true
          forward_gh: true
          forward_ssh: false
        dotfiles:
          repo: "https://github.com/you/dotfiles"
          script: "install.sh"
```

## Security

Every container is created with hardened defaults:

- `--security-opt=no-new-privileges` — no privilege escalation via setuid
- `--memory=4g` — prevent host OOM
- `--pids-limit=256` — prevent fork bombs
- Docker's default capability set (allows package installation and user creation inside the container)
- Bridge networking (not host)
- Never privileged by default

## Architecture

See [DESIGN.md](DESIGN.md) for the full design document and [docs/PLAN.md](docs/PLAN.md) for the implementation plan.

```
amplifier-bundle-containers/
├── bundle.md              # Thin root bundle
├── behaviors/
│   ├── containers.yaml    # Core: tool + agent + context
│   └── container-safety.yaml  # Optional safety hooks
├── agents/
│   └── container-operator.md  # Complex orchestration specialist
├── context/
│   ├── container-awareness.md  # Root session (thin)
│   └── container-guide.md      # Operator agent (comprehensive)
├── modules/
│   ├── tool-containers/        # Core tool module
│   └── hooks-container-safety/ # Optional safety hooks
└── images/
    └── amplifier-base/         # Optional curated Dockerfile
```

## License

MIT
