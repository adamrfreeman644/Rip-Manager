"""One-time Rip Node installation and adoption over SSH.

SSH and sudo credentials are intentionally request-only. They are never written
into SQLite, configuration files or logs. After adoption the manager talks to
the node only through the Rip Node HTTP API and its generated bearer token.
"""
from __future__ import annotations

import io
import os
import re
import secrets
import shlex
import socket
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
import paramiko
from fastapi import HTTPException

import db
import poller

BUNDLED_NODE = Path(__file__).resolve().parent / "bundled_rip_node_api_v0.2.5.py"
NODE_VERSION = "0.2.5"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SAFE_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
ProgressCallback = Callable[[str, int, str], None]


def _progress(callback: Optional[ProgressCallback], stage: str, percent: int, message: str) -> None:
    if callback:
        callback(stage, percent, message)


def _ssh(host: str, port: int, username: str, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host.strip(), port=port, username=username.strip(), password=password,
            timeout=10, banner_timeout=10, auth_timeout=10,
            look_for_keys=False, allow_agent=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH connection failed: {exc}") from exc
    return client


def _run(client: paramiko.SSHClient, command: str, sudo_password: Optional[str] = None,
         timeout: int = 120) -> tuple[int, str, str]:
    if sudo_password is not None:
        command = "sudo -S -p '' sh -c " + shlex.quote(command)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo_password is not None:
        stdin.write(sudo_password + "\n")
        stdin.flush()
    code = stdout.channel.recv_exit_status()
    return code, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")


def test_ssh(host: str, port: int, username: str, password: str,
             sudo_password: Optional[str]) -> dict:
    sudo = sudo_password if sudo_password is not None else password
    client = _ssh(host, port, username, password)
    try:
        _, hostname, _ = _run(client, "hostname")
        _, os_release, _ = _run(client, ". /etc/os-release 2>/dev/null; printf '%s %s' \"$NAME\" \"$VERSION_ID\"")
        py_rc, python3, _ = _run(client, "python3 --version 2>&1")
        sudo_rc, _, sudo_err = _run(client, "true", sudo)
        _, makemkv, _ = _run(client, "command -v makemkvcon || true")
        _, drives, _ = _run(client, "ls /dev/sr* 2>/dev/null || true")
        return {
            "ok": py_rc == 0 and sudo_rc == 0,
            "hostname": hostname.strip(),
            "os": os_release.strip() or "Unknown Linux",
            "python3": python3.strip() if py_rc == 0 else None,
            "sudo": sudo_rc == 0,
            "sudo_error": None if sudo_rc == 0 else sudo_err.strip()[-300:],
            "makemkv": bool(makemkv.strip()),
            "optical_devices": drives.split(),
        }
    finally:
        client.close()


def _updater_script(manager_url: str, api_port: int, token: str) -> str:
    manager = manager_url.rstrip("/")
    return f'''#!/usr/bin/env bash
set -euo pipefail
MODE="${{1:-check}}"
MANAGER={shlex.quote(manager)}
PORT={api_port}
TOKEN={shlex.quote(token)}
API=/opt/rip-node/rip_node_api.py
VENV=/opt/rip-node/venv
STATE=/var/lib/rip-node-github
BACKUPS=/opt/rip-node/backups
mkdir -p "$STATE" "$BACKUPS"
metadata="$(curl -fsS --connect-timeout 5 --max-time 30 "$MANAGER/updates/latest-node-version")"
available="$(printf '%s' "$metadata" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')"
filename="$(printf '%s' "$metadata" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("filename", ""))')"
printf '{{"state":"checked","available_version":"%s","filename":"%s","automatic_install":false,"checked_at":%s}}\n' "$available" "$filename" "$(date +%s)" >"$STATE/status.json"
[ "$MODE" = "check" ] && exit 0
[ "$MODE" = "install" ] || exit 2
request="$(curl -fsS --connect-timeout 5 --max-time 30 "$MANAGER/updates/node-install-request")"
pending="$(printf '%s' "$request" | python3 -c 'import json,sys; print(1 if json.load(sys.stdin).get("pending") else 0)')"
wanted="$(printf '%s' "$request" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')"
request_id="$(printf '%s' "$request" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("request_id", ""))')"
[ "$pending" = "1" ] || exit 0
[ "$wanted" = "$available" ] || exit 0
[ "$(cat "$STATE/last-request" 2>/dev/null || true)" != "$request_id" ] || exit 0
busy="$(curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/status" 2>/dev/null | python3 -c 'import json,sys; print(1 if json.load(sys.stdin).get("active_jobs") else 0)' 2>/dev/null || echo 1)"
[ "$busy" = "0" ] || {{ echo "Active rip detected; update deferred" >&2; exit 0; }}
if ps -C makemkvcon -C abcde -C cdparanoia -C icedax -C cdda2wav -o stat= 2>/dev/null | grep -Eqv '^[[:space:]]*Z'; then echo "Active rip process detected; update deferred" >&2; exit 0; fi
temporary="$(mktemp /tmp/rip-node-api.XXXXXX.py)"; trap 'rm -f "$temporary"' EXIT
curl -fsS --connect-timeout 5 --max-time 120 "$MANAGER/updates/files/latest-node" -o "$temporary"
[ -s "$temporary" ]; "$VENV/bin/python" -m py_compile "$temporary"
backup="$BACKUPS/rip_node_api-$(date +%Y%m%d-%H%M%S).py"; cp -a "$API" "$backup"
owner="$(stat -c %u "$API")"; group="$(stat -c %g "$API")"; install -o "$owner" -g "$group" -m 0644 "$temporary" "$API"
systemctl restart rip-node-api.service
healthy=0
for attempt in $(seq 1 45); do
  if curl -fsS --connect-timeout 2 --max-time 5 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then healthy=1; break; fi
  sleep 2
done
if [ "$healthy" != 1 ]; then cp -a "$backup" "$API"; systemctl restart rip-node-api.service; exit 1; fi
installed="$(curl -fsS "http://127.0.0.1:$PORT/api/info" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')"
if [ "$installed" != "$available" ]; then cp -a "$backup" "$API"; systemctl restart rip-node-api.service; exit 1; fi
printf '%s\n' "$request_id" >"$STATE/last-request"
curl -fsS -X POST -H 'Content-Type: application/json' --data "{{\"version\":\"$installed\",\"request_id\":\"$request_id\",\"node\":\"$(hostname)\"}}" "$MANAGER/updates/node-installed" >/dev/null
'''


def install_and_adopt(*, node_id: str, name: str, host: str, ssh_port: int,
                      username: str, password: str, sudo_password: Optional[str],
                      api_port: int, node_url: Optional[str], manager_url_for_node: str,
                      output_path: str, nas_share: Optional[str] = None,
                      nas_username: Optional[str] = None,
                      nas_password: Optional[str] = None,
                      progress: Optional[ProgressCallback] = None) -> dict:
    _progress(progress, "validate", 2, "Checking node and storage settings")
    if not SAFE_ID.fullmatch(node_id):
        raise HTTPException(status_code=422, detail="Node ID may only contain letters, numbers, dot, dash and underscore")
    if not SAFE_USER.fullmatch(username):
        raise HTTPException(status_code=422, detail="SSH username contains unsupported characters")
    if "\n" in name or "\r" in name:
        raise HTTPException(status_code=422, detail="Node name cannot contain line breaks")
    if not manager_url_for_node.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Manager URL must begin http:// or https://")
    if not output_path.startswith("/"):
        raise HTTPException(status_code=422, detail="Rip output path must be an absolute Linux path")
    if nas_share and not nas_share.startswith("//"):
        raise HTTPException(status_code=422, detail="NAS share must use //SERVER/SHARE format")

    sudo = sudo_password if sudo_password is not None else password
    token = secrets.token_urlsafe(32)
    _progress(progress, "connect", 7, f"Connecting securely to {host}")
    client = _ssh(host, ssh_port, username, password)
    try:
        sudo_rc, _, sudo_err = _run(client, "true", sudo)
        if sudo_rc != 0:
            raise HTTPException(status_code=403, detail=f"sudo authentication failed: {sudo_err.strip()[-200:]}")

        # Install only distribution packages we can obtain from normal Ubuntu repos.
        # MakeMKV itself is checked separately because its repository/licensing setup
        # varies; an existing installation is preserved.
        _progress(progress, "packages", 15, "Installing Ubuntu dependencies")
        apt = "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3-venv curl eject ffmpeg abcde cdparanoia cd-discid flac lame cifs-utils software-properties-common"
        rc, _, err = _run(client, apt, sudo, timeout=600)
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"Ubuntu package install failed: {err.strip()[-500:]}")

        # MakeMKV is supplied through its community Ubuntu PPA rather than the
        # standard Ubuntu archive. Failure is reported as a warning after the
        # otherwise usable audio node has been adopted.
        _progress(progress, "makemkv", 31, "Checking and installing MakeMKV")
        makemkv_install = (
            "command -v makemkvcon >/dev/null 2>&1 || { "
            "add-apt-repository -y ppa:heyarje/makemkv-beta >/dev/null 2>&1 && "
            "apt-get update -qq && apt-get install -y -qq makemkv-bin makemkv-oss; }"
        )
        _run(client, makemkv_install, sudo, timeout=600)

        # Remove every updater/service name used by older releases. This is
        # intentionally idempotent so a partially configured machine can be
        # safely re-run through Install & Adopt.
        _progress(progress, "cleanup", 39, "Removing obsolete node services")
        cleanup = (
            "systemctl disable --now rip-node-api.service rip-node-updater.timer rip-node-daily-updater.timer "
            "rip-github-node-check.timer rip-github-node-install.timer 2>/dev/null || true; "
            "rm -f /etc/systemd/system/rip-node-updater.service /etc/systemd/system/rip-node-updater.timer "
            "/etc/systemd/system/rip-node-daily-updater.service /etc/systemd/system/rip-node-daily-updater.timer "
            "/etc/systemd/system/rip-github-node-check.service /etc/systemd/system/rip-github-node-check.timer "
            "/etc/systemd/system/rip-github-node-install.service /etc/systemd/system/rip-github-node-install.timer "
            "/usr/local/sbin/rip-node-auto-updater /usr/local/sbin/rip-github-node-updater; "
            "systemctl daemon-reload"
        )
        _run(client, cleanup, sudo, timeout=120)

        if nas_share:
            _progress(progress, "storage", 47, "Configuring and testing NAS storage")
            credentials = (
                f"username={nas_username or ''}\npassword={nas_password or ''}\n"
                if nas_username else ""
            )
            sftp = client.open_sftp()
            try:
                if credentials:
                    with sftp.file("/tmp/rip-node-nas.credentials", "w") as f:
                        f.write(credentials)
            finally:
                sftp.close()
            options = (
                "credentials=/etc/rip-node/nas.credentials,"
                if credentials else "guest,"
            ) + f"uid={username},gid={username},file_mode=0664,dir_mode=0775,vers=3.0,nofail,_netdev,x-systemd.automount"
            mount_setup = (
                "mkdir -p /etc/rip-node " + shlex.quote(output_path) + "; "
                + ("install -m 0600 /tmp/rip-node-nas.credentials /etc/rip-node/nas.credentials; " if credentials else "")
                + "sed -i '\\# " + re.escape(output_path) + " cifs #d' /etc/fstab; "
                + "printf '%s %s cifs %s 0 0\\n' "
                + shlex.quote(nas_share) + " " + shlex.quote(output_path) + " " + shlex.quote(options)
                + " >>/etc/fstab; systemctl daemon-reload; mount " + shlex.quote(output_path)
            )
            rc, _, err = _run(client, mount_setup, sudo, timeout=120)
            if rc != 0:
                raise HTTPException(status_code=500, detail=f"NAS mount setup failed: {err.strip()[-500:]}")

        _progress(progress, "upload", 55, "Uploading the bundled Rip Node API")
        if not BUNDLED_NODE.is_file():
            raise HTTPException(status_code=500, detail=f"Bundled Node API is missing from Manager: {BUNDLED_NODE.name}")
        sftp = client.open_sftp()
        try:
            try:
                sftp.put(str(BUNDLED_NODE), "/tmp/rip_node_api.py")
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Node API upload failed: {exc}") from exc
        finally:
            sftp.close()

        _progress(progress, "python", 63, "Creating the Python environment")
        setup = (
            "mkdir -p /opt/rip-node /opt/rip-node/sounds " + shlex.quote(output_path) + "; "
            "chown " + shlex.quote(username) + " " + shlex.quote(output_path) + "; "
            "usermod -aG cdrom " + shlex.quote(username) + " 2>/dev/null || true; "
            "usermod -aG audio " + shlex.quote(username) + " 2>/dev/null || true; "
            "python3 -m venv /opt/rip-node/venv; "
            "/opt/rip-node/venv/bin/pip install -q --upgrade pip; "
            "/opt/rip-node/venv/bin/pip install -q fastapi==0.116.1 uvicorn==0.35.0 psutil; "
            "install -m 0644 /tmp/rip_node_api.py /opt/rip-node/rip_node_api.py"
        )
        rc, _, err = _run(client, setup, sudo, timeout=600)
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"Rip Node Python install failed: {err.strip()[-500:]}")

        # Build stable /dev/ripper/DVD* names from each drive's physical identity.
        _progress(progress, "drives", 73, "Creating persistent optical-drive mappings")
        _, sr_text, _ = _run(client, "ls /dev/sr* 2>/dev/null | sort -V || true")
        sr_devices = [item for item in sr_text.split() if item.startswith("/dev/sr")]
        rules = []
        for index, device in enumerate(sr_devices, 1):
            _, props, _ = _run(client, f"udevadm info --query=property --name={shlex.quote(device)}")
            properties = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
            key = "ID_PATH" if properties.get("ID_PATH") else "ID_SERIAL" if properties.get("ID_SERIAL") else None
            if key:
                value = properties[key].replace('"', '\\"')
                rules.append(f'SUBSYSTEM=="block", KERNEL=="sr*", ENV{{{key}}}=="{value}", SYMLINK+="ripper/DVD{index}"')
        if rules:
            sftp = client.open_sftp()
            try:
                with sftp.file("/tmp/99-rip-node-drives.rules", "w") as f:
                    f.write("\n".join(rules) + "\n")
            finally:
                sftp.close()
            rc, _, err = _run(client, "install -m 0644 /tmp/99-rip-node-drives.rules /etc/udev/rules.d/99-rip-node-drives.rules; udevadm control --reload-rules; udevadm trigger --subsystem-match=block; sleep 2", sudo)
            if rc != 0:
                raise HTTPException(status_code=500, detail=f"Optical drive mapping failed: {err.strip()[-500:]}")

        safe_name = name.replace("\\", "\\\\").replace('"', '\\"')
        safe_output = output_path.replace("\\", "\\\\").replace('"', '\\"')
        service = f'''[Unit]\nDescription=Rip Node FastAPI Backend\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nUser={username}\nWorkingDirectory=/opt/rip-node\nEnvironment="RIP_NODE_NAME={safe_name}"\nEnvironment="RIP_NODE_TOKEN={token}"\nEnvironment="RIP_NODE_PORT={api_port}"\nEnvironment="RIP_NODE_OUTPUT={safe_output}"\nExecStart=/opt/rip-node/venv/bin/python /opt/rip-node/rip_node_api.py\nRestart=on-failure\nRestartSec=3\nKillMode=mixed\nTimeoutStopSec=20\nSendSIGKILL=yes\n\n[Install]\nWantedBy=multi-user.target\n'''
        updater = _updater_script(manager_url_for_node, api_port, token)
        check_timer = '''[Unit]\nDescription=Check GitHub for Rip Node releases daily at midnight\n\n[Timer]\nOnCalendar=*-*-* 00:00:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n'''
        check_service = '''[Unit]\nDescription=Check GitHub for Rip Node releases without installing\nAfter=network-online.target\n\n[Service]\nType=oneshot\nExecStart=/usr/local/sbin/rip-github-node-updater check\n'''
        install_timer = '''[Unit]\nDescription=Poll Manager for a manual Node install request\n\n[Timer]\nOnBootSec=30sec\nOnUnitActiveSec=15sec\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n'''
        install_service = '''[Unit]\nDescription=Install a manually requested Rip Node release\nAfter=network-online.target rip-node-api.service\n\n[Service]\nType=oneshot\nTimeoutStartSec=5min\nExecStart=/usr/local/sbin/rip-github-node-updater install\n'''

        # Upload service text without ever exposing the password/token in shell logs.
        for path, content in (
            ("/tmp/rip-node-api.service", service),
            ("/tmp/rip-github-node-updater", updater),
            ("/tmp/rip-github-node-check.service", check_service),
            ("/tmp/rip-github-node-check.timer", check_timer),
            ("/tmp/rip-github-node-install.service", install_service),
            ("/tmp/rip-github-node-install.timer", install_timer),
        ):
            sftp = client.open_sftp()
            try:
                with sftp.file(path, "w") as f:
                    f.write(content)
            finally:
                sftp.close()

        _progress(progress, "services", 84, "Installing and starting node services")
        enable = (
            "install -m 0644 /tmp/rip-node-api.service /etc/systemd/system/rip-node-api.service; "
            "install -m 0700 /tmp/rip-github-node-updater /usr/local/sbin/rip-github-node-updater; "
            "install -m 0644 /tmp/rip-github-node-check.service /etc/systemd/system/rip-github-node-check.service; "
            "install -m 0644 /tmp/rip-github-node-check.timer /etc/systemd/system/rip-github-node-check.timer; "
            "install -m 0644 /tmp/rip-github-node-install.service /etc/systemd/system/rip-github-node-install.service; "
            "install -m 0644 /tmp/rip-github-node-install.timer /etc/systemd/system/rip-github-node-install.timer; "
            "systemctl daemon-reload; systemctl enable --now rip-node-api.service rip-github-node-check.timer rip-github-node-install.timer"
        )
        rc, _, err = _run(client, enable, sudo, timeout=120)
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"System service install failed: {err.strip()[-500:]}")

        # systemctl considers a service started as soon as its process has been
        # launched. Uvicorn may still need a few seconds before it binds port
        # 8000, especially on small nodes after creating a fresh virtualenv.
        # Wait locally on the node so adoption cannot fail during that gap.
        _progress(progress, "readiness", 90, "Waiting for the Node API to become ready")
        readiness = (
            f"for attempt in $(seq 1 45); do "
            f"curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:{api_port}/health >/dev/null 2>&1 && exit 0; "
            "sleep 2; done; "
            "echo 'Node API did not become ready'; "
            "systemctl status rip-node-api.service --no-pager -l || true; "
            "journalctl -u rip-node-api.service -n 40 --no-pager || true; exit 1"
        )
        rc, out, err = _run(client, readiness, sudo, timeout=120)
        if rc != 0:
            diagnostic = (out + "\n" + err).strip()[-1200:]
            raise HTTPException(status_code=500, detail=f"Rip Node service did not become ready: {diagnostic}")

        _, makemkv, _ = _run(client, "command -v makemkvcon || true")
        _, drives, _ = _run(client, "ls /dev/sr* 2>/dev/null || true")
    finally:
        client.close()

    manager_node_url = (node_url or f"http://{host}:{api_port}").rstrip("/")
    _progress(progress, "verify", 93, "Verifying the Node API from Rip Manager")
    last_error: Optional[Exception] = None
    info = None
    for attempt in range(30):
        try:
            response = httpx.get(f"{manager_node_url}/api/info", headers={"Authorization": f"Bearer {token}"}, timeout=8, trust_env=False)
            response.raise_for_status()
            info = response.json()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    if info is None:
        raise HTTPException(status_code=502, detail=(
            "Rip Node installed, but Rip Manager cannot reach its API at "
            f"{manager_node_url} after waiting 30 seconds. Check the Manager connection URL in Settings. Error: {last_error}"
        )) from last_error

    _progress(progress, "adopt", 97, "Saving the verified node in Rip Manager")
    with db.write() as conn:
        conn.execute(
            """INSERT INTO nodes(id,name,url,enabled,token,last_seen,online,last_error,last_poll)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,
               enabled=1,token=excluded.token,last_seen=excluded.last_seen,
               online=1,last_error=NULL,last_poll=excluded.last_poll""",
            (node_id, name, manager_node_url, 1, token, time.time(), 1, None, time.time()),
        )
    poller.wakeup.set()
    result = {
        "ok": True,
        "id": node_id,
        "name": name,
        "url": manager_node_url,
        "version": info.get("version"),
        "makemkv": bool(makemkv.strip()),
        "optical_devices": [f"/dev/ripper/DVD{i}" for i in range(1, len(sr_devices) + 1)],
        "warning": None if makemkv.strip() else "Node adopted, but MakeMKV is not installed yet; DVD video ripping will remain unavailable until makemkvcon is installed.",
    }
    _progress(progress, "complete", 100, "Node installed and adopted successfully")
    return result
