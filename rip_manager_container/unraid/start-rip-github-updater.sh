#!/bin/bash
mkdir -p /mnt/user/Updater/Logs /mnt/user/Updater/State
pgrep -f '[r]ip-github-manager-watcher.sh' >/dev/null || nohup bash /boot/config/rip-github/rip-github-manager-watcher.sh >>/mnt/user/Updater/Logs/github-watcher.log 2>&1 &
