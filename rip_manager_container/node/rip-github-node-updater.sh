#!/bin/bash
set -euo pipefail
MODE="${1:-check}"
MANAGER_URL="${RIP_MANAGER_URL:-http://192.168.1.187:8088}"; NODE_URL="${RIP_NODE_URL:-http://127.0.0.1:8000}"
NODE_FILE="${RIP_NODE_FILE:-/opt/rip-node/rip_node_api.py}"; PYTHON_BIN="${RIP_NODE_PYTHON:-/opt/rip-node/venv/bin/python}"
STATE=/var/lib/rip-node-github; BACKUPS=/opt/rip-node/backups; mkdir -p "$STATE" "$BACKUPS"
metadata="$(curl -fsS "$MANAGER_URL/updates/latest-node-version")"
available="$(printf %s "$metadata" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))')"
filename="$(printf %s "$metadata" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("filename",""))')"
printf '{"state":"checked","available_version":"%s","filename":"%s","automatic_install":false,"checked_at":%s}\n' "$available" "$filename" "$(date +%s)" >"$STATE/status.json"
[[ "$MODE" == check ]] && exit 0
request="$(curl -fsS "$MANAGER_URL/updates/node-install-request")"
pending="$(printf %s "$request" | python3 -c 'import json,sys; print("1" if json.load(sys.stdin).get("pending") else "0")')"
wanted="$(printf %s "$request" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')"
request_id="$(printf %s "$request" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("request_id", ""))')"
[[ "$pending" == 1 && "$wanted" == "$available" ]] || exit 0
[[ "$(cat "$STATE/last-request" 2>/dev/null || true)" != "$request_id" ]] || exit 0
if pgrep -f '(makemkvcon|abcde|cdparanoia|icedax|cdda2wav)' >/dev/null; then echo "Rip active; update deferred" >&2; exit 0; fi
tmp="$(mktemp /tmp/rip-node.XXXXXX.py)"; trap 'rm -f "$tmp"' EXIT
curl -fsSL "$MANAGER_URL/updates/files/latest-node" -o "$tmp"
"$PYTHON_BIN" -m py_compile "$tmp"
backup="$BACKUPS/rip_node_api-$(date +%Y%m%d-%H%M%S).py"; cp -a "$NODE_FILE" "$backup"
owner="$(stat -c %u "$NODE_FILE")"; group="$(stat -c %g "$NODE_FILE")"; install -o "$owner" -g "$group" -m 0644 "$tmp" "$NODE_FILE"
if systemctl restart rip-node-api && curl -fsS --retry 20 --retry-delay 2 "$NODE_URL/health" >/dev/null; then
  installed="$(curl -fsS "$NODE_URL/api/info" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))')"
  [[ "$installed" == "$available" ]] || { cp -a "$backup" "$NODE_FILE"; systemctl restart rip-node-api; exit 1; }
  printf '%s\n' "$request_id" >"$STATE/last-request"
  curl -fsS -X POST -H 'Content-Type: application/json' --data "{\"version\":\"$installed\",\"request_id\":\"$request_id\",\"node\":\"$(hostname)\"}" "$MANAGER_URL/updates/node-installed" >/dev/null || true
else cp -a "$backup" "$NODE_FILE"; systemctl restart rip-node-api; exit 1; fi
