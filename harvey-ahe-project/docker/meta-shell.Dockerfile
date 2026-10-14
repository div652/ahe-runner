# Sandbox image for the meta agents' run_shell_command (AHE-016).
# Runs with --network none, a read-only root filesystem, and only the
# agent's allowed roots mounted.
FROM python:3.13-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends git jq ripgrep file unzip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyyaml==6.0.2
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 GIT_CONFIG_GLOBAL=/tmp/.gitconfig \
    GIT_OPTIONAL_LOCKS=0
WORKDIR /tmp
