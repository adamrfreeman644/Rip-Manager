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
MANAGER_IMAGE="rip-manager:local"

mkdir -p "$STATE_DIR" "$BACKUPS" "$UPDATE_DIR/Logs"

echo '{"version":"6","rollback":true,"code_only_backups":true,"dedicated_container":true,"diagnostic_validation":true,"safe_self_update":true,"direct_docker_rebuild":true}' > "$CAPABILITIES"

log(){ printf '[rip-manager-updater] %s\n' "$*"; }

if ! docker version >/dev/null 2>&1; then
  log 'ERROR: Docker socket unavailable'
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
  rm -f "$PROJECT/Dockerfile" "$PROJECT/requirements.txt" "$PROJECT/simulator_node.py" "$PROJECT/README.md"
}

capture_manager_settings(){
  MANAGER_RESTART="$(docker inspect rip-manager --format '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null || true)"
  [[ -n "$MANAGER_RESTART" ]] || MANAGER_RESTART="unless-stopped"
}

start_manager(){
  cd "$PROJECT"
  capture_manager_settings
  log "Building $MANAGER_IMAGE directly from $HOST_PROJECT_DIR"
  if ! docker build -t "$MANAGER_IMAGE" "$HOST_PROJECT_DIR"; then
    log 'Manager image build failed'
    return 1
  fi

  docker rm -f rip-manager >/dev/null 2>&1 || true
  if ! docker run -d \
      --name rip-manager \
      --restart "$MANAGER_RESTART" \
      --label net.unraid.docker.managed=composeman \
      --label 'net.unraid.docker.webui=http://[IP]:[PORT:8080]' \
      --label net.unraid.docker.icon=/mnt/user/appdata/rip-manager/rip_manager_container/app/static/icons/remote-ripper-512.png \
      -p 8088:8080 \
      --add-host host.docker.internal:host-gateway \
      -v "$HOST_PROJECT_DIR/data:/data" \
      -v "$HOST_PROJECT_DIR/config:/config" \
      -v /mnt/user/Updater:/updates \
      -v /boot/config/rip-github/token:/run/secrets/github-token:ro \
      -e RIP_MANAGER_IDLE_POLL=5 \
      -e RIP_MANAGER_ACTIVE_POLL=2 \
      -e RIP_MANAGER_REQUEST_TIMEOUT=8 \
      -e RIP_MANAGER_UPDATES=/updates \
      -e RIP_GITHUB_OWNER="$OWNER" \
      -e RIP_GITHUB_MANAGER_REPO="$REPO" \
      -e RIP_MANAGER_COOKIE_SECURE=0 \
      --log-driver json-file \
      --log-opt max-size=10m \
      --log-opt max-file=3 \
      "$MANAGER_IMAGE"; then
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
  docker logs --tail 150 rip-manager 2>&1 | sed 's/^/[rip-manager] /' || true
  return 1
}

copy_release_code(){
  local source_dir="$1"
  clean_code
  cp -a "$source_dir/app" "$PROJECT/"
  [[ -d "$source_dir/node" ]] && cp -a "$source_dir/node" "$PROJECT/"
  [[ -d "$source_dir/unraid" ]] && cp -a "$source_dir/unraid" "$PROJECT/"
  cp -a "$source_dir/Dockerfile" "$PROJECT/"
  cp -a "$source_dir/requirements.txt" "$PROJECT/"
  cp -a "$source_dir/simulator_node.py" "$PROJECT/"
  [[ -f "$source_dir/README.md" ]] && cp -a "$source_dir/README.md" "$PROJECT/"
}

finalize_updater_files(){
  local source_dir="$1"
  [[ -f "$source_dir/docker-compose.yml" ]] && cp -a "$source_dir/docker-compose.yml" "$PROJECT/docker-compose.yml.next"
  [[ -f "$source_dir/updater.sh" ]] && cp -a "$source_dir/updater.sh" "$PROJECT/updater.sh.next"
  if [[ -f "$PROJECT/docker-compose.yml.next" ]]; then mv -f "$PROJECT/docker-compose.yml.next" "$PROJECT/docker-compose.yml"; fi
  if [[ -f "$PROJECT/updater.sh.next" ]]; then chmod 0755 "$PROJECT/updater.sh.next" && mv -f "$PROJECT/updater.sh.next" "$PROJECT/updater.sh"; fi
}

restore_archive(){
  local archive="$1" staging
  tar -tzf "$archive" >/dev/null
  staging="$(mktemp -d /tmp/rip-manager-restore.XXXXXX)"
  tar -C "$staging" -xzf "$archive"
  clean_code
  cp -a "$staging/app" "$PROJECT/" 2>/dev/null || true
  cp -a "$staging/node" "$PROJECT/" 2>/dev/null || true
  cp -a "$staging/unraid" "$PROJECT/" 2>/dev/null || true
  cp -a "$staging/Dockerfile" "$PROJECT/" 2>/dev/null || true
  cp -a "$staging/requirements.txt" "$PROJECT/" 2>/dev/null || true
  cp -a "$staging/simulator_node.py" "$PROJECT/" 2>/dev/null || true
  cp -a "$staging/README.md" "$PROJECT/" 2>/dev/null || true
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
  copy_release_code "$source_dir"

  if start_manager; then
    installed="$(current_version)"
    if [[ "$installed" == "$version" ]]; then
      finalize_updater_files "$source_dir"
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
