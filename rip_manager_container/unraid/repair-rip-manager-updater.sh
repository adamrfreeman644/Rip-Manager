#!/bin/bash
set -euo pipefail

PROJECT="${RIP_MANAGER_PROJECT_DIR:-/mnt/user/appdata/rip-manager/rip_manager_container}"
STATE_DIR="${RIP_MANAGER_STATE_DIR:-/mnt/user/Updater/State}"
UPDATE_DIR="$(dirname "$STATE_DIR")"
TOKEN_FILE="${RIP_GITHUB_TOKEN_FILE:-/boot/config/rip-github/token}"

log(){ printf '[repair-rip-manager-updater] %s\n' "$*"; }
fail(){ log "ERROR: $*"; exit 1; }

[[ $EUID -eq 0 ]] || fail "Run this script as root on the Unraid host"
[[ -d "$PROJECT" ]] || fail "Rip Manager project not found at $PROJECT"
[[ -f "$PROJECT/docker-compose.yml" ]] || fail "docker-compose.yml not found in $PROJECT"
[[ -f "$PROJECT/updater.sh" ]] || fail "updater.sh not found in $PROJECT"
[[ -f "$TOKEN_FILE" ]] || fail "GitHub token not found at $TOKEN_FILE"
command -v docker >/dev/null 2>&1 || fail "Docker is not available"
docker compose version >/dev/null 2>&1 || fail "Docker Compose is not available on the Unraid host"

mkdir -p "$STATE_DIR" "$UPDATE_DIR/Backups" "$UPDATE_DIR/Logs"
chmod 0755 "$PROJECT/updater.sh"

log "Project: $PROJECT"
log "Stopping any stale updater container"
docker rm -f rip-manager-updater >/dev/null 2>&1 || true

# Remove a request left behind by an updater that was not actually running.
# The user will queue a fresh install from the Manager UI after this repair.
if [[ -f "$UPDATE_DIR/install-manager.request.json" ]]; then
  mv -f "$UPDATE_DIR/install-manager.request.json" "$UPDATE_DIR/install-manager.request.stale.$(date +%s).json"
  log "Moved stale install request aside"
fi

# Clear only updater status/capability state. Manager data/config are untouched.
rm -f "$STATE_DIR/github-manager-update.json" "$STATE_DIR/host-updater-capabilities.json"

log "Validating Compose configuration"
cd "$PROJECT"
HOST_PROJECT_DIR="$PROJECT" docker compose config >/dev/null

log "Recreating the updater sidecar from the current on-disk 0.20.3/0.20.9-style Compose definition"
HOST_PROJECT_DIR="$PROJECT" docker compose up -d --force-recreate --no-deps rip-manager-updater

log "Waiting for updater startup"
for _ in $(seq 1 30); do
  if docker inspect -f '{{.State.Running}}' rip-manager-updater 2>/dev/null | grep -qx true; then
    if docker exec rip-manager-updater docker version >/dev/null 2>&1 && docker exec rip-manager-updater docker compose version >/dev/null 2>&1; then
      break
    fi
  fi
  sleep 2
done

running="$(docker inspect -f '{{.State.Running}}' rip-manager-updater 2>/dev/null || true)"
[[ "$running" == "true" ]] || {
  log "Updater failed to stay running. Last logs:"
  docker logs --tail 120 rip-manager-updater 2>&1 || true
  exit 1
}

docker exec rip-manager-updater docker version >/dev/null 2>&1 || {
  log "Updater cannot access the Docker socket. Last logs:"
  docker logs --tail 120 rip-manager-updater 2>&1 || true
  exit 1
}

docker exec rip-manager-updater docker compose version >/dev/null 2>&1 || {
  log "Docker Compose is unavailable inside the updater container. Last logs:"
  docker logs --tail 120 rip-manager-updater 2>&1 || true
  exit 1
}

# The updater writes this shortly after startup. Give it a moment so the UI can
# distinguish a live updater from a stale request.
for _ in $(seq 1 15); do
  [[ -f "$STATE_DIR/host-updater-capabilities.json" ]] && break
  sleep 1
done

log "Updater container is running and Docker/Compose access is healthy"
log "Current updater logs:"
docker logs --tail 40 rip-manager-updater 2>&1 || true
log "Repair complete. Open Rip Manager, press Check now, then install 0.20.9 once."
