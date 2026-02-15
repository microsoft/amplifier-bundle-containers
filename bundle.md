---
bundle:
  name: containers
  version: 0.1.0
  description: General-purpose container management for Amplifier agents

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: containers:behaviors/containers
  - bundle: containers:behaviors/container-safety
---

# Container Management

@containers:context/container-awareness.md
