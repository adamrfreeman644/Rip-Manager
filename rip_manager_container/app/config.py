"""Environment, paths and tunables for Rip Manager.

Every setting is read once at import so the rest of the application can rely
on plain constants instead of scattering os.getenv calls through the code.
"""

from __future__ import annotations

import os
from pathlib import Path

VERSION = "0.18.2"

DATA_DIR = Path(os.getenv("RIP_MANAGER_DATA", "/data"))
CONFIG_DIR = Path(os.getenv("RIP_MANAGER_CONFIG", "/config"))
UPDATE_DIR = Path(os.getenv("RIP_MANAGER_UPDATES", "/updates"))
GITHUB_OWNER = os.getenv("RIP_GITHUB_OWNER", "Adamrfreeman644")
GITHUB_MANAGER_REPO = os.getenv("RIP_GITHUB_MANAGER_REPO", "rip-manager")
GITHUB_TOKEN_FILE = Path(os.getenv("RIP_GITHUB_TOKEN_FILE", "/run/secrets/github-token"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
BUNDLED_NODE_VERSION = "0.2.6"
BUNDLED_NODE_FILENAME = f"rip_node_api_v{BUNDLED_NODE_VERSION}.py"
BUNDLED_NODE_FILE = Path(__file__).resolve().parent / "bundled_rip_node_api_v0.2.6.py"

DB_PATH = DATA_DIR / "rip-manager.db"
NODES_FILE = CONFIG_DIR / "nodes.json"

DEFAULT_IDLE_POLL = int(os.getenv("RIP_MANAGER_IDLE_POLL", "5"))
DEFAULT_ACTIVE_POLL = int(os.getenv("RIP_MANAGER_ACTIVE_POLL", "2"))

# Polling must fail fast. A node that has wedged should mark itself offline
# long before the next poll cycle begins, never stack requests on top of it.
REQUEST_TIMEOUT = float(os.getenv("RIP_MANAGER_REQUEST_TIMEOUT", "8"))

# Commands are given a longer read budget than polls because ejecting or
# closing a tray can take a few seconds on a tired drive, but never unlimited.
COMMAND_CONNECT_TIMEOUT = float(os.getenv("RIP_MANAGER_COMMAND_CONNECT_TIMEOUT", "5"))
COMMAND_READ_TIMEOUT = float(os.getenv("RIP_MANAGER_COMMAND_READ_TIMEOUT", "60"))

SESSION_COOKIE = "rip_manager_session"
SESSION_SECONDS = int(os.getenv("RIP_MANAGER_SESSION_SECONDS", str(7 * 24 * 60 * 60)))
SESSION_COOKIE_SECURE = os.getenv("RIP_MANAGER_COOKIE_SECURE", "0") == "1"

# A four-digit PIN is only ten thousand combinations, so unlimited guessing
# would be trivially brute-forced over a LAN. Attempts are throttled per client.
LOGIN_MAX_ATTEMPTS = int(os.getenv("RIP_MANAGER_LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_SECONDS = float(os.getenv("RIP_MANAGER_LOGIN_LOCKOUT", "60"))

# History retention. The manager runs indefinitely, so both tables are capped.
MAX_JOB_HISTORY = int(os.getenv("RIP_MANAGER_MAX_JOB_HISTORY", "2000"))
MAX_EVENTS = int(os.getenv("RIP_MANAGER_MAX_EVENTS", "2000"))

# UPC lookups are cached because the free tier is rate limited and the same
# box set gets scanned once per disc.
UPC_LOOKUP_URL = os.getenv("RIP_MANAGER_UPC_URL", "https://api.upcitemdb.com/prod/trial/lookup")
UPC_TIMEOUT = float(os.getenv("RIP_MANAGER_UPC_TIMEOUT", "8"))
UPC_CACHE_SECONDS = float(os.getenv("RIP_MANAGER_UPC_CACHE", str(90 * 24 * 60 * 60)))

# The GUI is served from the same origin, so cross-origin access is off unless
# it is explicitly requested.
CORS_ORIGINS = [o.strip() for o in os.getenv("RIP_MANAGER_CORS_ORIGINS", "").split(",") if o.strip()]

ACTIVE_JOB_STATES = {"starting", "ripping", "verifying", "cancelling"}
FINISHED_JOB_STATES = {"complete", "failed", "verification_failed", "cancelled"}
RETRYABLE_JOB_STATES = {"failed", "verification_failed", "cancelled", "interrupted"}

MANAGER_PREFIX = "rip_manager_container_v"
MANAGER_SUFFIX = ".zip"
NODE_PREFIX = "rip_node_api_v"
NODE_SUFFIX = ".py"
NODE_UPDATER_PREFIX = "rip-node-auto-updater_v"
NODE_UPDATER_SUFFIX = ".sh"


def ensure_directories() -> None:
    """Create the writable directories, tolerating a read-only update share."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

UPDATE_SYSTEM_PREFIX = "rip_update_system_v"
UPDATE_SYSTEM_SUFFIX = ".zip"
