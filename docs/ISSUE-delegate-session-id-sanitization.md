# Bug: Delegate Tool Constructs Invalid Session IDs for Namespaced Agents

## Summary

The delegate tool (`amplifier_module_tool_delegate`) constructs session IDs using raw agent names without sanitization. When an agent name contains `/` or `:` (common for bundle-defined agents like `containers:agents/container-operator`), the resulting session ID is rejected by the `SessionStore` path-traversal protection, making delegation to any namespaced agent impossible.

## Error

```
ValueError: Invalid session_id: 5ca563ef-94d8-448a-a96d-fb2454ad1a49-139e4d5e2de94206_containers:agents/container-operator
```

## Reproduction

1. Add any bundle with a namespaced agent (e.g., `amplifier-bundle-containers` with `containers:agents/container-operator`)
2. In a session, delegate to that agent:
   ```
   delegate(agent="containers:agents/container-operator", instruction="test")
   ```
3. Immediate `ValueError: Invalid session_id` — the agent never loads

## Root Cause

### Where the ID is constructed (broken)

**`amplifier-foundation/modules/tool-delegate/amplifier_module_tool_delegate/__init__.py` ~line 774-776:**

```python
child_span = uuid.uuid4().hex[:16]
sub_session_id = f"{parent_session_id}-{child_span}_{agent_name}"
```

For `agent_name = "containers:agents/container-operator"`, this produces:
```
5ca563ef-...-139e4d5e2de94206_containers:agents/container-operator
                                         ^      ^
                                         colon  slash — both problematic
```

### Where it crashes

**`amplifier-app-cli/amplifier_app_cli/session_store.py` ~line 117-119** (repeated in multiple methods):

```python
if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
    raise ValueError(f"Invalid session_id: {session_id}")
```

The `/` in `agents/container-operator` triggers the path-traversal protection.

### The sanitizer that already exists (but is bypassed)

**`amplifier-foundation/amplifier_foundation/tracing.py` ~line 72-84** has a sanitization function:

```python
# Replaces non-alphanumeric chars with hyphens
sanitized = re.sub(r"[^a-z0-9]+", "-", raw_name)
```

It even has a **passing test** proving it works for namespaced agents:

```python
def test_colon_in_agent_name_sanitized(self):
    sub_id = generate_sub_session_id(agent_name="foundation:explorer")
    assert sub_id.endswith("_foundation-explorer")
    assert ":" not in sub_id
```

`containers:agents/container-operator` would become `containers-agents-container-operator` — perfectly safe.

### Why the sanitizer is bypassed

The `session_spawner` calls `generate_sub_session_id()` only when no ID is pre-provided:

```python
if not sub_session_id:  # skipped because delegate tool already set it
    sub_session_id = generate_sub_session_id(...)
```

Since the delegate tool always pre-generates the ID at line ~776, the sanitizer never runs.

## Proposed Fix

In `amplifier_module_tool_delegate/__init__.py`, replace the inline ID construction with the existing sanitizing function:

```python
# BEFORE (broken):
child_span = uuid.uuid4().hex[:16]
sub_session_id = f"{parent_session_id}-{child_span}_{agent_name}"

# AFTER (fixed):
from amplifier_foundation.tracing import generate_sub_session_id
sub_session_id = generate_sub_session_id(
    agent_name=agent_name,
    parent_session_id=parent_session_id,
)
```

This:
- Eliminates duplicate ID generation logic
- Ensures all agent names are sanitized to filesystem-safe strings
- Handles colons, slashes, and any other special characters
- Is already tested (see `test_colon_in_agent_name_sanitized`)

## Impact

- **Affects**: Any bundle-defined agent with a namespaced path (e.g., `namespace:agents/agent-name`)
- **Severity**: Medium — delegation to these agents is completely broken
- **Workaround**: None — the error occurs before the agent session is created
- **Known affected bundles**: `amplifier-bundle-containers` (`containers:agents/container-operator`)

## Secondary Consideration

The `SessionStore` only rejects `/` and `\` but not `:`. On Windows, colons are also invalid in file paths. The `tracing.py` sanitizer handles this correctly by stripping all non-alphanumeric characters, making the fix future-proof for Windows support.

## Discovered By

Found during integration testing of `amplifier-bundle-containers` (February 2026). The container-operator agent could never be delegated to because every delegation attempt hit this error.

## Related Files

| File | Role |
|------|------|
| `amplifier-foundation/modules/tool-delegate/amplifier_module_tool_delegate/__init__.py` | Bug location (~line 776) |
| `amplifier-foundation/amplifier_foundation/tracing.py` | Existing sanitizer (~line 72) |
| `amplifier-app-cli/amplifier_app_cli/session_store.py` | Crash location (~line 117) |
