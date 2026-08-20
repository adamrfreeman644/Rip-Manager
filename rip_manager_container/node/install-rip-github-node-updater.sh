#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
install -m 0755 "$here/rip-github-node-updater.sh" /usr/local/sbin/rip-github-node-updater
install -m 0644 "$here"/*.service "$here"/*.timer /etc/systemd/system/
systemctl disable --now rip-node-updater.timer rip-node-daily-updater.timer 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now rip-github-node-check.timer rip-github-node-install.timer
