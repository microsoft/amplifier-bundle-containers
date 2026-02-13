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
| `amplifier` | Amplifier pre-installed + all credentials forwarded |
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

### Container Lifecycle

| Operation | Description |
|-----------|-------------|
| `preflight` | Check Docker/Podman readiness |
| `create` | Create a provisioned container |
| `exec` | Run commands inside a container |
| `exec_interactive_hint` | Get the connect command for the user |
| `list` / `status` | See what's running |
| `snapshot` / `restore` | Save and restore container state |
| `copy_in` / `copy_out` | Transfer files |
| `destroy` / `destroy_all` | Clean up |

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

- `--cap-drop=ALL` — all Linux capabilities dropped
- `--security-opt=no-new-privileges` — no privilege escalation
- `--memory=4g` — prevent host OOM
- `--pids-limit=256` — prevent fork bombs
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
