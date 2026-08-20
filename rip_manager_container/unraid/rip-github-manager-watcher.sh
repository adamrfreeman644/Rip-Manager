#!/bin/bash
set -u
SCRIPT=/boot/config/rip-github/rip-github-manager-updater.sh
STATE=/mnt/user/Updater/State
mkdir -p "$STATE"
while true; do
  [[ -f /mnt/user/Updater/rollback-manager.request.json ]] && bash "$SCRIPT" rollback >>/mnt/user/Updater/Logs/github-updater.log 2>&1
  [[ -f /mnt/user/Updater/install-manager.request.json ]] && bash "$SCRIPT" install >>/mnt/user/Updater/Logs/github-updater.log 2>&1
  today="$(date +%F)"; hour="$(date +%H)"; last="$(cat "$STATE/last-github-check-day" 2>/dev/null || true)"
  if [[ "$hour" == 00 && "$last" != "$today" ]]; then bash "$SCRIPT" check >>/mnt/user/Updater/Logs/github-updater.log 2>&1 && printf '%s\n' "$today" >"$STATE/last-github-check-day"; fi
  sleep 5
done
