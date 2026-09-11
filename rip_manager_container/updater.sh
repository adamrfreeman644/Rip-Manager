#!/usr/bin/env bash
set -euo pipefail

TOKEN_FILE="${RIP_GITHUB_TOKEN_FILE:-/run/secrets/github-token}"
OWNER="${RIP_GITHUB_OWNER:-Adamrfreeman644}"
REPO="${RIP_GITHUB_MANAGER_REPO:-rip-manager}"
PROJECT="${RIP_MANAGER_PROJECT_DIR:-/project}"
STATE_DIR="${RIP_MANAGER_STATE_DIR:-/updates/State}"
UPDATE_DIR="$(dirname "$STATE_DIR")"
BACKUPS="$UPDATE_DIR/Backups"
REQUEST="$UPDATE_DIR/install-manager.request.json"
ROLLBACK_REQUEST="$UPDATE_DIR/rollback-manager.request.json"
STATUS="$STATE_DIR/github-manager-update.json"
CAPABILITIES="$STATE_DIR/host-updater-capabilities.json"

mkdir -p "$STATE_DIR" "$BACKUPS" "$UPDATE_DIR/Logs"

echo '{"version":"4","rollback":true,"code_only_backups":true,"dedicated_container":true,"diagnostic_validation":true}' > "$CAPABILITIES"

log(){ printf '[rip-manager-updater] %s\n' "$*"; }

if ! docker version >/dev/null 2>&1; then
  log 'ERROR: Docker socket unavailable'
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  log 'ERROR: Docker Compose plugin unavailable'
  exit 1
fi
if [[ ! -f "$TOKEN_FILE" ]]; then
  log "ERROR: GitHub token missing at $TOKEN_FILE"
  exit 1
fi

HOST_PROJECT_DIR="$(docker inspect rip-manager-updater --format '{{range .Mounts}}{{if eq .Destination "/project"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
if [[ -z "$HOST_PROJECT_DIR" ]]; then
  log 'ERROR: Could not determine host project path'
  exit 1
fi
export HOST_PROJECT_DIR

current_version(){
  docker exec rip-manager python -c 'import urllib.request,json; print(json.load(urllib.request.urlopen("http://127.0.0.1:8080/api/info",timeout=3)).get("version","unknown"))' 2>/dev/null || printf unknown
}

write_status(){
  local state="$1" message="$2" version="${3:-}"
  jq -n --arg state "$state" --arg message "$message" --arg version "$version" --argjson updated_at "$(date +%s)" \
    '{state:$state,message:$message,version:$version,updated_at:$updated_at}' > "$STATUS"
}

create_code_backup(){
  local version="${1:-unknown}" safe stamp archive sidecar
  safe="$(printf '%s' "$version" | tr -cd '0-9A-Za-z._-')"; [[ -n "$safe" ]] || safe=unknown
  stamp="$(date +%Y%m%d-%H%M%S)"
  archive="$BACKUPS/rip-manager-code-v${safe}-${stamp}.tar.gz"
  tar -C "$PROJECT" --exclude='./data' --exclude='./config' -czf "$archive" .
  tar -tzf "$archive" >/dev/null
  sidecar="${archive%.tar.gz}.json"
  jq -n --arg filename "$(basename "$archive")" --arg version "$version" --argjson created_at "$(date +%s)" --argjson bytes "$(stat -c %s "$archive")" \
    '{filename:$filename,version:$version,created_at:$created_at,bytes:$bytes,verified:true,code_only:true}' > "$sidecar"
  mapfile -t old < <(ls -1t "$BACKUPS"/rip-manager-code-*.tar.gz 2>/dev/null | tail -n +6 || true)
  for item in "${old[@]}"; do rm -f "$item" "${item%.tar.gz}.json"; done
  printf '%s' "$archive"
}

clean_code(){
  rm -rf "$PROJECT/app" "$PROJECT/node" "$PROJECT/unraid"
  rm -f "$PROJECT/Dockerfile" "$PROJECT/docker-compose.yml" "$PROJECT/requirements.txt" "$PROJECT/simulator_node.py" "$PROJECT/README.md" "$PROJECT/updater.sh"
}

start_manager(){
  cd "$PROJECT"
  # Do not force-refresh the base image on every application update. A registry
  # timeout should not turn an otherwise valid Rip Manager release into a
  # failed update. Normal Docker cache behaviour is enough here.
  if ! docker compose build rip-manager; then
    log 'Manager image build failed'
    return 1
  fi
  if ! docker compose up -d --no-deps rip-manager; then
    log 'Manager container failed to start'
    return 1
  fi
  for _ in $(seq 1 90); do
    if docker exec rip-manager python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/health",timeout=2).read()' >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  log 'Manager health endpoint did not become ready in time'
  docker logs --tail 80 rip-manager 2>&1 | sed 's/^/[rip-manager] /' || true
  return 1
}

restore_archive(){
  local archive="$1" staging
  tar -tzf "$archive" >/dev/null
  staging="$(mktemp -d /tmp/rip-manager-restore.XXXXXX)"
  tar -C "$staging" -xzf "$archive"
  clean_code
  cp -a "$staging/." "$PROJECT/"
  rm -rf "$staging"
  start_manager
}

install_requested(){
  [[ -f "$REQUEST" ]] || return 0
  local token metadata version asset_id wanted tmp source_dir backup installed
  token="$(<"$TOKEN_FILE")"
  wanted="$(jq -r '.version // ""' "$REQUEST")"
  write_status running "Installing Rip Manager v$wanted" "$wanted"
  metadata="$(curl -fsS -H "Authorization: Bearer $token" -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$OWNER/$REPO/releases/latest")"
  version="$(jq -r '.tag_name // ""' <<<"$metadata" | sed 's/^v//')"
  asset_id="$(jq -r '.assets[0].id // empty' <<<"$metadata")"
  if [[ -z "$version" || "$wanted" != "$version" || -z "$asset_id" ]]; then
    write_status failed 'Requested release no longer matches GitHub latest release' "$wanted"
    return 1
  fi
  tmp="$(mktemp -d /tmp/rip-manager-update.XXXXXX)"
  trap 'rm -rf "$tmp"' RETURN
  curl -fsSL -H "Authorization: Bearer $token" -H 'Accept: application/octet-stream' "https://api.github.com/repos/$OWNER/$REPO/releases/assets/$asset_id" -o "$tmp/release.zip"
  unzip -tq "$tmp/release.zip" >/dev/null
  unzip -oq "$tmp/release.zip" -d "$tmp/release"
  source_dir="$tmp/release/rip_manager_container"
  [[ -d "$source_dir" && -f "$source_dir/docker-compose.yml" && -f "$source_dir/app/config.py" ]] || {
    write_status failed 'Release archive is missing Rip Manager files' "$wanted"; return 1;
  }
  backup="$(create_code_backup "$(current_version)")"
  clean_code
  cp -a "$source_dir/." "$PROJECT/"
  if start_manager; then
    installed="$(current_version)"
    if [[ "$installed" == "$version" ]]; then
      rm -f "$REQUEST"
      write_status success "Rip Manager v$version installed successfully" "$version"
      log "Installed v$version"
      return 0
    fi
    log "Health passed but version check returned '$installed' instead of '$version'"
  fi
  log 'New version failed validation; restoring previous code'
  restore_archive "$backup" || true
  write_status failed "v$version failed validation; previous code restored" "$version"
  return 1
}

rollback_requested(){
  [[ -f "$ROLLBACK_REQUEST" ]] || return 0
  local filename target archive safety
  filename="$(jq -r '.filename // ""' "$ROLLBACK_REQUEST")"
  target="$(jq -r '.version // "unknown"' "$ROLLBACK_REQUEST")"
  [[ "$filename" =~ ^rip-manager-code-v[0-9A-Za-z._-]+-[0-9]{8}-[0-9]{6}\.tar\.gz$ ]] || {
    write_status rollback_failed 'Invalid rollback backup name' "$target"; rm -f "$ROLLBACK_REQUEST"; return 1;
  }
  archive="$BACKUPS/$filename"
  [[ -f "$archive" ]] || { write_status rollback_failed 'Rollback backup not found' "$target"; rm -f "$ROLLBACK_REQUEST"; return 1; }
  write_status rollback_running "Rolling back Rip Manager to v$target" "$target"
  safety="$(create_code_backup "$(current_version)")"
  if restore_archive "$archive"; then
    rm -f "$ROLLBACK_REQUEST"
    write_status rollback_success "Rollback to v$target completed successfully" "$target"
  else
    restore_archive "$safety" || true
    write_status rollback_failed "Rollback to v$target failed; safety copy restored" "$target"
    return 1
  fi
}

write_status idle 'Dedicated Rip Manager updater ready' "$(current_version)"
log "Ready; project host path is $HOST_PROJECT_DIR"

while true; do
  rollback_requested || true
  install_requested || true
  sleep 3
done
