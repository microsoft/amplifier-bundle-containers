---
bundle:
  name: containers
  version: 0.2.0
  description: General-purpose container management for Amplifier agents

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: containers:behaviors/containers
  - bundle: containers:behaviors/container-safety
---

# Container Management

For container orchestration patterns and Docker/Incus operations, delegate to
`container-operator` — it carries the full container-awareness reference.

<!-- container-awareness.md previously @-mentioned here AND in behaviors/containers.yaml
     (double-load). Now loaded only via the container-operator agent body
     (context-sink pattern). -->

