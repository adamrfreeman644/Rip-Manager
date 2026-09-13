"""SQLite access, schema, migrations and the settings cache.

Connection handling is the important part of this module. Version 0.7.1 called
``sqlite3.connect`` on every helper and used ``with db() as conn``, which
commits but never closes, so every settings read leaked a connection and a file
handle until the garbage collector happened to run. Here each thread keeps one
connection for its lifetime, writers are serialised behind a lock, and settings
are cached in memory so the authentication middleware never touches disk.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from config import (
    DB_PATH,
    DEFAULT_ACTIVE_POLL,
    DEFAULT_IDLE_POLL,
    MAX_EVENTS,
    MAX_JOB_HISTORY,
    NODES_FILE,
)

_local = threading.local()
_write_lock = threading.Lock()
_settings_cache: Dict[str, str] = {}
_settings_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    token TEXT,
    last_seen REAL,
    online INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_poll REAL
);

CREATE TABLE IF NOT EXISTS node_cache (
    node_id TEXT PRIMARY KEY,
    drives_json TEXT,
    jobs_json TEXT,
    stats_json TEXT,
    fetched_at REAL
);

CREATE TABLE IF NOT EXISTS jobs_history (
    manager_job_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    node_job_id TEXT,
    drive TEXT,
    state TEXT,
    title TEXT,
    year INTEGER,
    season INTEGER,
    disc INTEGER,
    barcode TEXT,
    media_type TEXT,
    creator TEXT,
    narrator TEXT,
    started_at REAL,
    finished_at REAL,
    progress REAL,
    last_message TEXT,
    output_dir TEXT,
    verification_json TEXT,
    raw_json TEXT,
    cleared INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT,
    node_job_id TEXT,
    drive TEXT,
    event_type TEXT NOT NULL,
    created_at REAL NOT NULL,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

-- Wait for Disc. One row per drive holds the details the operator typed
-- before any media was loaded; the poller starts the rip when a disc appears.
CREATE TABLE IF NOT EXISTS pending_intake (
    node_id TEXT NOT NULL,
    drive TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    season INTEGER,
    disc INTEGER,
    barcode TEXT,
    media_type TEXT,
    creator TEXT,
    narrator TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    ready_at REAL,
    PRIMARY KEY (node_id, drive)
);

CREATE TABLE IF NOT EXISTS upc_cache (
    barcode TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS physical_media (
    manager_job_id TEXT PRIMARY KEY,
    user_text TEXT NOT NULL DEFAULT '',
    front_image TEXT,
    rear_image TEXT,
    extras_json TEXT NOT NULL DEFAULT '[]',
    final_dir TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(manager_job_id) REFERENCES jobs_history(manager_job_id)
);

CREATE TABLE IF NOT EXISTS transfer_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_job_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'queued',
    source_dir TEXT,
    destination_dir TEXT,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    bytes_copied INTEGER NOT NULL DEFAULT 0,
    files_total INTEGER NOT NULL DEFAULT 0,
    files_copied INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(manager_job_id) REFERENCES jobs_history(manager_job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_node_state ON jobs_history(node_id, state);
CREATE INDEX IF NOT EXISTS idx_jobs_drive ON jobs_history(node_id, drive, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs_history(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON auth_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_transfer_state_created ON transfer_queue(state, created_at);
"""

DEFAULT_SETTINGS = {
    "idle_poll_seconds": str(DEFAULT_IDLE_POLL),
    "active_poll_seconds": str(DEFAULT_ACTIVE_POLL),
    "theme": "dark",
    "sounds": "1",
    "volume": "75",
    "auto_eject": "1",
    "confirm_eject": "1",
    "drive_preferences": "{}",
    "simulation": "1",
    "lock_enabled": "0",
    "upc_lookup": "1",
    "metadata_musicbrainz": "1",
    "metadata_google_books": "1",
    "metadata_google_books_key": "",
    "metadata_upcitemdb": "1",
    "metadata_upcitemdb_mode": "free",
    "metadata_upcitemdb_key": "",
    "metadata_omdb": "1",
    "metadata_omdb_key": "",
    "minimum_video_minutes": "2",
    "verify_before_eject": "1",
    "prefer_english_audio": "1",
    "prefer_english_subtitles": "1",
    "dashboard_columns": "3",
    "dashboard_rows": "2",
    "dashboard_tiles": "[]",
    "dashboard_spacing_percent": "100",
    "mover_enabled": "0",
    "mover_delete_source": "1",
    "mover_destination_root": "/media",
    "mover_node_mounts": "{}",
    "mover_node_source_roots": "{}",
    "mover_destination_folders": "{\"movie\":\"Movies\",\"tv\":\"TV\",\"music\":\"Music\",\"audiobook\":\"Audiobooks\"}",
}


def connection() -> sqlite3.Connection:
    """Return this thread's connection, opening it on first use."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """Serialised write transaction. SQLite allows exactly one writer."""
    conn = connection()
    with _write_lock:
        with conn:
            yield conn


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return connection().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    return connection().execute(sql, params).fetchone()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    with write() as conn:
        conn.executescript(SCHEMA)

        # Columns added after 0.7.1. Existing databases are upgraded in place
        # so job history and node configuration survive the update.
        job_columns = _columns(conn, "jobs_history")
        for column, definition in (
            ("barcode", "TEXT"),
            ("media_type", "TEXT"),
            ("creator", "TEXT"),
            ("narrator", "TEXT"),
            ("auto_started", "INTEGER NOT NULL DEFAULT 0"),
            ("cleared", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in job_columns:
                conn.execute(f"ALTER TABLE jobs_history ADD COLUMN {column} {definition}")

        pending_columns = _columns(conn, "pending_intake")
        for column, definition in (
            ("creator", "TEXT"),
            ("narrator", "TEXT"),
            ("ready_at", "REAL"),
        ):
            if column not in pending_columns:
                conn.execute(f"ALTER TABLE pending_intake ADD COLUMN {column} {definition}")

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, value))

        # 0.7.0 briefly used passwords. Reset that old format safely so the
        # owner can configure the four-digit PIN without being locked out.
        old_password = conn.execute("SELECT value FROM settings WHERE key='password_hash'").fetchone()
        pin_hash = conn.execute("SELECT value FROM settings WHERE key='pin_hash'").fetchone()
        if old_password and not pin_hash:
            conn.execute("DELETE FROM settings WHERE key='password_hash'")
            conn.execute("UPDATE settings SET value='0' WHERE key='lock_enabled'")

        # Upgrade the old untouched default without overriding a custom value.
        conn.execute("UPDATE settings SET value='5' WHERE key='idle_poll_seconds' AND value='10'")

        # v0.17.6 gives the two simulated Blu-ray drives BR names. Migrate
        # saved tile positions and preferences once without disturbing layout.
        migration = conn.execute(
            "SELECT value FROM settings WHERE key='simulator_drive_names_v0176'"
        ).fetchone()
        if not migration:
            drive_names = {
                "simulator:DVD1": "simulator:BR1",
                "simulator:DVD2": "simulator:BR2",
                "simulator:DVD3": "simulator:DVD1",
                "simulator:DVD4": "simulator:DVD2",
                "simulator:DVD5": "simulator:DVD3",
                "simulator:DVD6": "simulator:DVD4",
            }
            tiles_row = conn.execute("SELECT value FROM settings WHERE key='dashboard_tiles'").fetchone()
            preferences_row = conn.execute("SELECT value FROM settings WHERE key='drive_preferences'").fetchone()
            try:
                tiles = json.loads(tiles_row["value"] if tiles_row else "[]")
            except json.JSONDecodeError:
                tiles = []
            try:
                preferences = json.loads(preferences_row["value"] if preferences_row else "{}")
            except json.JSONDecodeError:
                preferences = {}
            tiles = [drive_names.get(value, value) for value in tiles]
            preferences = {drive_names.get(key, key): value for key, value in preferences.items()}
            conn.execute("UPDATE settings SET value=? WHERE key='dashboard_tiles'", (json.dumps(tiles),))
            conn.execute("UPDATE settings SET value=? WHERE key='drive_preferences'", (json.dumps(preferences),))
            conn.execute(
                "INSERT INTO settings(key,value) VALUES ('simulator_drive_names_v0176','1')"
            )

    refresh_settings_cache()


def refresh_settings_cache() -> None:
    rows = query("SELECT key,value FROM settings")
    with _settings_lock:
        _settings_cache.clear()
        _settings_cache.update({row["key"]: row["value"] for row in rows})


def get_setting(key: str, default: str = "") -> str:
    with _settings_lock:
        return _settings_cache.get(key, default)


def get_setting_int(key: str, default: int) -> int:
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


def get_setting_bool(key: str, default: bool = False) -> bool:
    return get_setting(key, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def get_setting_json(key: str, default: Any) -> Any:
    try:
        return json.loads(get_setting(key, "") or "null") or default
    except json.JSONDecodeError:
        return default


def set_settings(values: Dict[str, Any]) -> None:
    """Write several settings and keep the in-memory cache in step."""
    clean = {k: str(v) for k, v in values.items() if v is not None}
    if not clean:
        return
    with write() as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    with _settings_lock:
        _settings_cache.update(clean)


def set_setting(key: str, value: Any) -> None:
    set_settings({key: value})


def load_nodes_file() -> None:
    """Seed the node table from config/nodes.json on a fresh installation."""
    if not NODES_FILE.exists():
        NODES_FILE.write_text(
            json.dumps(
                {"nodes": [{
                    "id": "simulator",
                    "name": "Simulator Node",
                    "url": "http://127.0.0.1:8080/simulator-node",
                    "enabled": True,
                    "token": None,
                }]},
                indent=2,
            ),
            encoding="utf-8",
        )
    try:
        payload = json.loads(NODES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    with write() as conn:
        for node in payload.get("nodes", []):
            if not node.get("id") or not node.get("url"):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO nodes(id,name,url,enabled,token) VALUES (?,?,?,?,?)",
                (
                    node["id"],
                    node.get("name", node["id"]),
                    node["url"].rstrip("/"),
                    1 if node.get("enabled", True) else 0,
                    node.get("token"),
                ),
            )


def prune_history() -> None:
    """Cap the two unbounded tables and drop expired sessions."""
    now = time.time()
    with write() as conn:
        conn.execute(
            """
            DELETE FROM jobs_history WHERE manager_job_id IN (
                SELECT manager_job_id FROM jobs_history
                WHERE manager_job_id NOT IN (SELECT manager_job_id FROM physical_media)
                  AND manager_job_id NOT IN (SELECT manager_job_id FROM transfer_queue)
                ORDER BY COALESCE(started_at, updated_at) DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (MAX_JOB_HISTORY,),
        )
        conn.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (MAX_EVENTS,),
        )
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
