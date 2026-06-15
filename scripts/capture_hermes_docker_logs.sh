#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="${HERMES_LOG_DIR:-/opt/data/logs}"
CONTAINER="${HERMES_AGENT_CONTAINER:-hermes-agent}"
SINCE="${HERMES_DOCKER_LOG_SINCE:-26h}"

mkdir -p "$LOG_DIR"

tmp="$(mktemp "${LOG_DIR}/hermes-agent-docker.XXXXXX.tmp")"
trap 'rm -f "$tmp"' EXIT

docker logs --timestamps --since "$SINCE" "$CONTAINER" >"$tmp" 2>&1

install -m 0644 "$tmp" "${LOG_DIR}/hermes-agent-docker.log"
install -m 0644 "$tmp" "${LOG_DIR}/gateway.log"
install -m 0644 "$tmp" "${LOG_DIR}/agent.log"
