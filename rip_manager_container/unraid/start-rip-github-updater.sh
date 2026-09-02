#!/bin/bash
set -euo pipefail

USER_ROOT="/mnt/user"
LOG_DIR="$USER_ROOT/Updater/Logs"
STATE_DIR="$USER_ROOT/Updater/State"

# Never create directories under /mnt/user until Unraid has mounted the
# user-share filesystem. Creating them early can prevent the real mount from
# being established and make shares/Docker appear to disappear.
wait_for_user_shares() {
  local timeout="${UNRAID_USER_SHARE_WAIT_SECONDS:-300}"
  local waited=0

  while (( waited < timeout )); do
    if mountpoint -q "$USER_ROOT"; then
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done

  echo "[rip-github-updater] /mnt/user did not become a mountpoint within ${timeout}s; not starting updater." >&2
  return 1
}

wait_for_user_shares || exit 0

mkdir -p "$LOG_DIR" "$STATE_DIR"
pgrep -f '[r]ip-github-manager-watcher.sh' >/dev/null || \
  nohup bash /boot/config/rip-github/rip-github-manager-watcher.sh >>"$LOG_DIR/github-watcher.log" 2>&1 &
