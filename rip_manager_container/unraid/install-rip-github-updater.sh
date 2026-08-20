#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
install -d -m 700 /boot/config/rip-github
install -m 0755 "$here/rip-github-manager-updater.sh" /boot/config/rip-github/
install -m 0755 "$here/rip-github-manager-watcher.sh" /boot/config/rip-github/
install -m 0755 "$here/start-rip-github-updater.sh" /boot/config/rip-github/
mkdir -p /mnt/user/Updater/State
printf '{"version":"2","rollback":true,"code_only_backups":true,"installed_at":%s}\n' "$(date +%s)" >/mnt/user/Updater/State/host-updater-capabilities.json
pkill -f '[r]ip-manager-updater-daemon.sh' 2>/dev/null || true
pkill -f '[r]ip-github-manager-watcher.sh' 2>/dev/null || true
sed -i '\#/boot/config/rip-manager-updater/start-rip-manager-updater.sh#d' /boot/config/go
grep -Fq '/boot/config/rip-github/start-rip-github-updater.sh' /boot/config/go || printf '%s\n' 'bash /boot/config/rip-github/start-rip-github-updater.sh' >>/boot/config/go
bash /boot/config/rip-github/start-rip-github-updater.sh
