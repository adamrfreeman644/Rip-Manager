"""Job history and the event stream the GUI polls for toasts and sounds."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from config import ACTIVE_JOB_STATES, FINISHED_JOB_STATES
import db

_INSERT = """
INSERT INTO jobs_history(
    manager_job_id, node_id, node_job_id, drive, state, title, year, season, disc,
    barcode, media_type, creator, narrator, started_at, finished_at, progress,
    last_message, output_dir, verification_json, raw_json, auto_started, updated_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

_LIVE_COLUMNS = """
    drive=excluded.drive,
    state=excluded.state,
    started_at=excluded.started_at,
    finished_at=excluded.finished_at,
    progress=excluded.progress,
    last_message=excluded.last_message,
    output_dir=excluded.output_dir,
    verification_json=excluded.verification_json,
    raw_json=excluded.raw_json,
    updated_at=excluded.updated_at
"""

# Polling must never overwrite the title, year, season or disc the operator
# typed, because the node does not know them on older firmware.
UPSERT_SQL = _INSERT + "ON CONFLICT(manager_job_id) DO UPDATE SET" + _LIVE_COLUMNS

# Start Rip and Wait for Disc do know the metadata, so their insert owns those
# descriptive columns as well.
UPSERT_METADATA_SQL = _INSERT + """
ON CONFLICT(manager_job_id) DO UPDATE SET
    title=excluded.title,
    year=excluded.year,
    season=excluded.season,
    disc=excluded.disc,
    barcode=excluded.barcode,
    media_type=excluded.media_type,
    creator=excluded.creator,
    narrator=excluded.narrator,
    auto_started=excluded.auto_started,
""" + _LIVE_COLUMNS


def add_event(conn: sqlite3.Connection, node_id: str, node_job_id: Optional[str],
              drive: Optional[str], event_type: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events(node_id,node_job_id,drive,event_type,created_at,payload_json) "
        "VALUES (?,?,?,?,?,?)",
        (node_id, node_job_id, drive, event_type, time.time(), json.dumps(payload)),
    )


def clear_drive(node_id: str, drive: str) -> None:
    """Hide finished job cards while retaining physical-media history."""
    with db.write() as conn:
        conn.execute(
            "UPDATE jobs_history SET cleared=1 WHERE node_id=? AND drive=? AND state NOT IN ('starting','ripping','verifying','cancelling')",
            (node_id, drive.upper()),
        )


def _row_values(node_id: str, job: dict, metadata: dict, auto_started: bool) -> tuple:
    job_id = str(job.get("id") or "")
    return (
        f"{node_id}:{job_id}", node_id, job_id, (job.get("drive") or "").upper(), job.get("state"),
        metadata.get("title"), metadata.get("year"), metadata.get("season"), metadata.get("disc"),
        metadata.get("barcode"), metadata.get("media_type"), metadata.get("creator"),
        metadata.get("narrator"),
        job.get("started_at"), job.get("finished_at"), job.get("progress"), job.get("last_message"),
        job.get("output_dir"), json.dumps(job.get("verification")), json.dumps(job),
        1 if auto_started else 0, time.time(),
    )


def upsert_polled_job(conn: sqlite3.Connection, node_id: str, job: dict) -> bool:
    """Record a job seen during polling, keeping the metadata we already hold.

    A node that has been restarted mid-set may report a job we have never seen.
    In that case its own ``media`` block (Rip Node 0.1.16 and newer) is used so
    the tile still shows a title instead of a bare folder name.
    """
    job_id = str(job.get("id") or "")
    if not job_id:
        return False
    manager_job_id = f"{node_id}:{job_id}"

    existing = conn.execute(
        "SELECT state,title,year,season,disc,barcode,media_type,creator,narrator,auto_started "
        "FROM jobs_history WHERE manager_job_id=?",
        (manager_job_id,),
    ).fetchone()

    if existing:
        metadata = {k: existing[k] for k in (
            "title", "year", "season", "disc", "barcode", "media_type", "creator", "narrator"
        )}
        auto_started = bool(existing["auto_started"])
    else:
        reported = job.get("media") or {}
        metadata = {
            "title": reported.get("title"),
            "year": reported.get("year"),
            "season": reported.get("season"),
            "disc": reported.get("disc"),
            "barcode": reported.get("barcode"),
            "media_type": reported.get("media_type"),
            "creator": reported.get("creator"),
            "narrator": reported.get("narrator"),
        }
        auto_started = False

    previous_state = existing["state"] if existing else None
    conn.execute(UPSERT_SQL, _row_values(node_id, job, metadata, auto_started))

    state = job.get("state")
    if state in FINISHED_JOB_STATES and state != previous_state:
        add_event(conn, node_id, job_id, job.get("drive"), state, job)
    return state == "complete" and previous_state != "complete"


def record_started_job(node_id: str, drive: str, metadata: dict, result: dict,
                       auto_started: bool = False) -> Optional[str]:
    """Store the operator-supplied details against a job we just started."""
    job = result.get("job") if isinstance(result, dict) else None
    if not job or not job.get("id"):
        return None
    values = _row_values(node_id, {**job, "drive": drive}, metadata, auto_started)
    with db.write() as conn:
        conn.execute(UPSERT_METADATA_SQL, values)
        if auto_started:
            add_event(conn, node_id, str(job["id"]), drive.upper(), "auto_started", {
                "message": f"{drive.upper()} started automatically: {metadata.get('title')}",
                "title": metadata.get("title"),
            })
    return str(job["id"])


def latest_job_for_drive(node_id: str, drive: str) -> Optional[sqlite3.Row]:
    return db.query_one(
        "SELECT * FROM jobs_history WHERE node_id=? AND drive=? "
        "ORDER BY COALESCE(started_at, updated_at) DESC LIMIT 1",
        (node_id, drive.upper()),
    )


def mark_missing_jobs_interrupted(conn: sqlite3.Connection, node_id: str, reported_ids: set[str],
                                  now: float) -> None:
    """A node restart silently kills its rips; reflect that instead of showing
    a progress bar that will never move again."""
    placeholders = ",".join("?" for _ in ACTIVE_JOB_STATES)
    stale = conn.execute(
        f"SELECT manager_job_id,node_job_id,drive FROM jobs_history "
        f"WHERE node_id=? AND state IN ({placeholders})",
        (node_id, *sorted(ACTIVE_JOB_STATES)),
    ).fetchall()
    for old in stale:
        if str(old["node_job_id"]) in reported_ids:
            continue
        message = "The rip stopped being reported after the node restarted"
        conn.execute(
            "UPDATE jobs_history SET state='interrupted',finished_at=?,last_message=?,updated_at=? "
            "WHERE manager_job_id=?",
            (now, message, now, old["manager_job_id"]),
        )
        add_event(conn, node_id, old["node_job_id"], old["drive"], "interrupted", {"message": message})


def row_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["verification"] = json.loads(item.pop("verification_json") or "null")
    item["raw"] = json.loads(item.pop("raw_json") or "{}")
    item["auto_started"] = bool(item.get("auto_started"))
    item["cleared"] = bool(item.get("cleared"))
    return item
