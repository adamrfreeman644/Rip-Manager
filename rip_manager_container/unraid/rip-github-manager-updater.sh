#!/bin/bash
set -euo pipefail
MODE="${1:-check}"
TOKEN_FILE="${RIP_GITHUB_TOKEN_FILE:-/boot/config/rip-github/token}"
OWNER="${RIP_GITHUB_OWNER:-Adamrfreeman644}"
REPO="${RIP_GITHUB_MANAGER_REPO:-rip-manager}"
PROJECT="${RIP_MANAGER_PROJECT_DIR:-/mnt/user/appdata/rip-manager/rip_manager_container}"
STATE_DIR="${RIP_MANAGER_STATE_DIR:-/mnt/user/Updater/State}"
UPDATE_DIR="$(dirname "$STATE_DIR")"; BACKUPS="$UPDATE_DIR/Backups"
REQUEST="$UPDATE_DIR/install-manager.request.json"; ROLLBACK_REQUEST="$UPDATE_DIR/rollback-manager.request.json"
STATUS="$STATE_DIR/github-manager-update.json"
mkdir -p "$STATE_DIR" "$BACKUPS" "$UPDATE_DIR/Logs"
project_parent="$(dirname "$PROJECT")"; project_name="$(basename "$PROJECT")"
[[ -n "$PROJECT" && "$project_name" == "rip_manager_container" && -d "$PROJECT" ]]

current_version() {
  curl -fsS --max-time 3 http://127.0.0.1:8088/api/info 2>/dev/null | php -r '$d=json_decode(stream_get_contents(STDIN),true); echo $d["version"]??"unknown";' || printf unknown
}
write_status() {
  php -r '$d=["state"=>$argv[2],"message"=>$argv[3],"version"=>$argv[4],"updated_at"=>time()]; file_put_contents($argv[1],json_encode($d).PHP_EOL);' "$STATUS" "$1" "$2" "${3:-}"
}
create_code_backup() {
  local version="${1:-unknown}" safe timestamp archive sidecar size
  safe="$(printf '%s' "$version" | tr -cd '0-9A-Za-z._-')"; [[ -n "$safe" ]] || safe=unknown
  timestamp="$(date +%Y%m%d-%H%M%S)"; archive="$BACKUPS/rip-manager-code-v${safe}-${timestamp}.tar.gz"
  tar -C "$project_parent" --exclude="$project_name/data" --exclude="$project_name/config" -czf "$archive" "$project_name"
  tar -tzf "$archive" >/dev/null; size="$(stat -c %s "$archive")"; sidecar="${archive%.tar.gz}.json"
  php -r '$d=["filename"=>$argv[2],"version"=>$argv[3],"created_at"=>time(),"bytes"=>(int)$argv[4],"verified"=>true,"code_only"=>true]; file_put_contents($argv[1],json_encode($d).PHP_EOL);' "$sidecar" "$(basename "$archive")" "$version" "$size"
  mapfile -t old < <(ls -1t "$BACKUPS"/rip-manager-code-*.tar.gz 2>/dev/null | tail -n +6 || true)
  for item in "${old[@]}"; do rm -f "$item" "${item%.tar.gz}.json"; done
  printf '%s' "$archive"
}
clean_code() {
  rm -rf "$PROJECT/app" "$PROJECT/node" "$PROJECT/unraid"
  rm -f "$PROJECT/Dockerfile" "$PROJECT/docker-compose.yml" "$PROJECT/requirements.txt" "$PROJECT/simulator_node.py" "$PROJECT/README.md"
}
restore_archive() {
  local archive="$1" staging source; tar -tzf "$archive" >/dev/null
  staging="$(mktemp -d /tmp/rip-manager-restore.XXXXXX)"; tar -C "$staging" -xzf "$archive"
  source="$staging/$project_name"; [[ -d "$source" ]]
  (cd "$PROJECT" && docker compose down) || true
  clean_code
  tar -C "$source" --exclude='./data' --exclude='./config' -cf - . | tar -C "$PROJECT" -xf -
  rm -rf "$staging"
  cd "$PROJECT" && docker compose up -d --build
  curl -fsS --retry 30 --retry-delay 2 --retry-all-errors http://127.0.0.1:8088/health >/dev/null
}

if [[ "$MODE" == rollback ]]; then
  [[ -f "$ROLLBACK_REQUEST" ]]
  filename="$(php -r '$d=json_decode(file_get_contents($argv[1]),true); echo $d["filename"]??"";' "$ROLLBACK_REQUEST")"
  [[ "$filename" =~ ^rip-manager-code-v[0-9A-Za-z._-]+-[0-9]{8}-[0-9]{6}\.tar\.gz$ || "$filename" =~ ^rip-manager-[0-9]{8}-[0-9]{6}\.tar\.gz$ ]]
  archive="$BACKUPS/$filename"; [[ -f "$archive" ]]
  target="$(php -r '$d=json_decode(file_get_contents($argv[1]),true); echo $d["version"]??"unknown";' "$ROLLBACK_REQUEST")"
  write_status rollback_running "Rolling back Rip Manager to v$target" "$target"
  safety="$(create_code_backup "$(current_version)")"
  if restore_archive "$archive"; then
    rm -f "$ROLLBACK_REQUEST"; write_status rollback_success "Rollback to v$target completed successfully" "$target"
  else
    write_status rollback_failed "Rollback failed; restoring the version that was running before it" "$target"
    restore_archive "$safety" || true; exit 1
  fi
  exit 0
fi

token="$(<"$TOKEN_FILE")"; api="https://api.github.com/repos/$OWNER/$REPO/releases/latest"
metadata="$(curl -fsS -H "Authorization: Bearer $token" -H 'Accept: application/vnd.github+json' "$api")"
version="$(printf %s "$metadata" | php -r '$d=json_decode(stream_get_contents(STDIN),true); echo ltrim($d["tag_name"]??"","v");')"
asset_id="$(printf %s "$metadata" | php -r '$d=json_decode(stream_get_contents(STDIN),true); echo $d["assets"][0]["id"]??"";')"
filename="$(printf %s "$metadata" | php -r '$d=json_decode(stream_get_contents(STDIN),true); echo $d["assets"][0]["name"]??"";')"
php -r '$d=["state"=>"checked","available_version"=>$argv[2],"filename"=>$argv[3],"automatic_install"=>false,"checked_at"=>time()]; file_put_contents($argv[1],json_encode($d).PHP_EOL);' "$STATUS" "$version" "$filename"
[[ "$MODE" == check ]] && exit 0
[[ "$MODE" == install && -f "$REQUEST" ]] || exit 0
wanted="$(php -r '$d=json_decode(file_get_contents($argv[1]),true); echo $d["version"]??"";' "$REQUEST")"
[[ "$wanted" == "$version" && -n "$asset_id" ]] || { echo "Requested release no longer matches GitHub" >&2; exit 1; }
tmp="$(mktemp -d /tmp/rip-manager-github.XXXXXX)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL -H "Authorization: Bearer $token" -H 'Accept: application/octet-stream' "https://api.github.com/repos/$OWNER/$REPO/releases/assets/$asset_id" -o "$tmp/release.zip"
unzip -tq "$tmp/release.zip" >/dev/null; unzip -oq "$tmp/release.zip" -d "$tmp/release"
source_dir="$tmp/release/rip_manager_container"; [[ -d "$source_dir" ]]
backup="$(create_code_backup "$(current_version)")"
(cd "$PROJECT" && docker compose down) || true; clean_code; cp -a "$source_dir/." "$PROJECT/"
if cd "$PROJECT" && docker compose up -d --build && curl -fsS --retry 30 --retry-delay 2 --retry-all-errors http://127.0.0.1:8088/health >/dev/null; then
  if [[ -d /boot/config/rip-github && -f "$PROJECT/unraid/rip-github-manager-updater.sh" ]]; then
    install -m 0755 "$PROJECT/unraid/rip-github-manager-updater.sh" /boot/config/rip-github/
    install -m 0755 "$PROJECT/unraid/rip-github-manager-watcher.sh" /boot/config/rip-github/
    install -m 0755 "$PROJECT/unraid/start-rip-github-updater.sh" /boot/config/rip-github/
  fi
  rm -f "$REQUEST"; write_status success "Rip Manager v$version installed successfully" "$version"
else
  restore_archive "$backup" || true; write_status failed "v$version failed its health check; previous code restored" "$version"; exit 1
fi
