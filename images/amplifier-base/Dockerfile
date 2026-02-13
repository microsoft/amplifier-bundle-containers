FROM python:3.12-slim

LABEL maintainer="Amplifier Project"
LABEL org.opencontainers.image.source="https://github.com/microsoft/amplifier-bundle-containers"
LABEL org.opencontainers.image.description="Base image for Amplifier container environments"

# Avoid interactive prompts during package install
ENV DEBIAN_FRONTEND=noninteractive

# Install common development tools
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        git \
        curl \
        wget \
        jq \
        tree \
        less \
        make \
        build-essential \
        vim-tiny \
        ca-certificates \
        openssh-client \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN pip install --no-cache-dir --quiet uv

# Set up locale
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Create workspace directory
RUN mkdir -p /workspace
WORKDIR /workspace

# Default command keeps container running for exec
CMD ["sleep", "infinity"]
