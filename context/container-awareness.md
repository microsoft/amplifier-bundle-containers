# Container Management

You have access to the `containers` tool for creating and managing isolated container environments (Docker/Podman).

## When to Use

- User wants to try something safely without affecting their host
- Parallel isolated workloads are needed
- A clean development environment is requested
- Building, testing, or running untrusted code
- Service prototyping (databases, caches, app servers)
- Any scenario requiring isolation from the host environment

## Quick Start

```
1. containers(operation="preflight")              # Verify Docker/Podman ready
2. containers(operation="create", purpose="python") # Create environment
3. containers(operation="exec", container="...", command="...")  # Work inside it
4. containers(operation="exec_interactive_hint", container="...")  # Hand off to user
5. containers(operation="destroy", container="...")  # Clean up when done
```

## Operations

| Operation | Use For |
|-----------|---------|
| `preflight` | Check if container runtime is ready (auto-runs before first create) |
| `create` | Create a new container with smart defaults |
| `exec` | Run a command inside a container |
| `exec_interactive_hint` | Get the shell command for user to connect |
| `list` | Show all managed containers |
| `status` | Detailed container status |
| `destroy` | Remove a container |
| `destroy_all` | Remove all managed containers |
| `copy_in` / `copy_out` | Transfer files between host and container |
| `snapshot` / `restore` | Save and restore container state |
| `create_network` / `destroy_network` | Docker networks for multi-container communication |
| `cache_clear` | Remove cached purpose images (one or all) |
| `exec_background` | Start a long-running command, returns a job_id |
| `exec_poll` | Check status and get output of a background job |
| `exec_cancel` | Kill a background job |
| `wait_healthy` | Poll health-check command until service is ready |

## The `purpose` Parameter

Use `purpose` on `create` to get smart defaults instead of specifying everything:

| Purpose | What You Get |
|---------|-------------|
| `"python"` | Python 3.12 + uv + venv |
| `"node"` | Node 20 + corepack |
| `"rust"` | Rust toolchain + build tools |
| `"go"` | Go 1.22 toolchain |
| `"general"` | Ubuntu 24.04 + common dev tools |
| `"amplifier"` | Python + Amplifier pre-installed + all credentials + settings forwarded |
| `"try-repo"` | Auto-detect language from repo, clone and set up (requires `repo_url`) |
| `"clean"` | Pristine environment — no dotfiles, no credential forwarding |

## Convenience Features (on `create`)

These make the container feel like home with zero effort:

| Parameter | Default | What It Does |
|-----------|---------|-------------|
| `env_passthrough` | `"auto"` | Forwards API keys matching common patterns |
| `forward_git` | `true` | Copies .gitconfig so git works naturally |
| `forward_gh` | `true` | Forwards GH CLI auth for private repos |
| `forward_ssh` | `false` | Mounts ~/.ssh read-only (opt-in) |
| `dotfiles_repo` / `dotfiles_inline` | config | Clones a dotfiles repo or writes inline files |
| `mount_cwd` | `true` | Mounts current directory into /workspace |
| `as_root` | `false` | Run exec as root for admin operations (package install, system changes) |
| `repo_url` | none | Git URL to clone (used with `purpose="try-repo"`) |
| `cache_bust` | `false` | Force fresh build, ignoring cached purpose image |
| `amplifier_version` | latest | Pin Amplifier version (`purpose="amplifier"` only) |
| `amplifier_bundle` | none | Bundle URI to configure inside container (`purpose="amplifier"` only) |
| `compose_content` | none | Docker Compose YAML for multi-service infrastructure |
| `compose_file` | none | Path to existing docker-compose.yml on host |
| `repos` | none | Git repos to clone: [{url, path, install}] |
| `config_files` | none | Files to write: {"/path": "content"} |

## Patterns

### Agent Puppet Mode
Create container, work inside it, report results:
```
create -> exec (multiple commands) -> report to user -> destroy
```

### User Handoff Mode
Create container, set it up, give user the connect command:
```
create -> exec (setup) -> exec_interactive_hint -> tell user the command
```

### Hybrid Mode
Set up for user, user works, agent assists further:
```
create -> exec (setup) -> hand off -> user works -> user asks for help -> exec
```

### Parallel Agents (Background Execution)
Create multiple containers, run tasks concurrently:
```
create(purpose="amplifier", name="agent-a", ...)
create(purpose="amplifier", name="agent-b", ...)
exec_background(container="agent-a", command="amplifier run 'task 1'")
exec_background(container="agent-b", command="amplifier run 'task 2'")
exec_poll(container="agent-a", job_id="...")
exec_poll(container="agent-b", job_id="...")
```

### Compose + Provisioned Workspace
Start infrastructure via compose, work in a fully provisioned primary container:
```
create(name="my-stack",
    compose_content="services:\n  db:\n    image: postgres:16\n  ...",
    purpose="python",
    repos=[{"url": "https://github.com/user/app", "path": "/workspace/app", "install": "pip install -e ."}],
    config_files={"/workspace/.env": "DATABASE_URL=postgresql://db:5432/app\n"},
    forward_gh=True)
```
Compose manages infrastructure (Postgres, Redis). Our tool manages the workspace with full provisioning.

## Important

- The provisioning report is returned from `create` — no need to investigate what was set up
- Use `as_root=True` on `exec` for admin operations (package installation, system changes)
- Container runs as root for setup; `exec` runs as the mapped host user for correct file ownership
- Image caching makes the second `create` with the same purpose much faster
- Always provide `exec_interactive_hint` output when handing a container to the user
- Use `purpose` to let the tool choose smart defaults — don't over-specify
- Containers are ephemeral by default — destroyed on session end
- Set `persistent=true` if the container should survive session restarts
- `compose_content` lets the LLM write docker-compose.yml naturally (it already knows how)
- `repos` clones multiple git repos with optional install commands
- `config_files` writes arbitrary files to any path in the container
- `destroy` automatically tears down compose services alongside the primary container
- For complex multi-container setups, delegate to the `container-operator` agent
