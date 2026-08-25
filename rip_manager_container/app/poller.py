"""The background poll loop.

Responsibilities:
- ask every enabled node for health, drives, jobs and statistics
- cache the last good answer so a slow node never blanks the GUI
- keep job history and the event stream up to date
- start any Wait for Disc request as soon as its disc appears
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from typing import List, Optional

from config import ACTIVE_JOB_STATES, DEFAULT_ACTIVE_POLL, DEFAULT_IDLE_POLL
import db
import intake
import jobs as job_history
import nodes as node_client

log = logging.getLogger("rip-manager.poller")

wakeup = asyncio.Event()
_task: Optional[asyncio.Task] = None


def has_active_jobs(job_list: List[dict]) -> bool:
    return any(job.get("state") in ACTIVE_JOB_STATES for job in job_list)


def _store_node_results(node_id: str, now: float, drives_result, jobs_result, stats_result) -> None:
    """Persist one poll. Partial failures reuse the previous good payload."""
    drives_ok = isinstance(drives_result, list)
    jobs_ok = isinstance(jobs_result, list)
    stats_ok = isinstance(stats_result, dict)

    warnings = []
    for label, ok, result in (
        ("Drive information", drives_ok, drives_result),
        ("Jobs", jobs_ok, jobs_result),
        ("Statistics", stats_ok, stats_result),
    ):
        if not ok:
            detail = str(result).strip() or type(result).__name__
            warnings.append(f"{label} temporarily unavailable: {detail}")

    with db.write() as conn:
        previous = conn.execute(
            "SELECT drives_json,jobs_json,stats_json,fetched_at FROM node_cache WHERE node_id=?",
            (node_id,),
        ).fetchone()

        drives_json = json.dumps(drives_result) if drives_ok else (previous["drives_json"] if previous else "[]")
        jobs_json = json.dumps(jobs_result) if jobs_ok else (previous["jobs_json"] if previous else "[]")
        stats_json = json.dumps(stats_result) if stats_ok else (previous["stats_json"] if previous else "{}")
        fetched_at = now if (drives_ok or jobs_ok or stats_ok) else (previous["fetched_at"] if previous else now)

        conn.execute(
            """
            INSERT INTO node_cache(node_id,drives_json,jobs_json,stats_json,fetched_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET
                drives_json=excluded.drives_json, jobs_json=excluded.jobs_json,
                stats_json=excluded.stats_json, fetched_at=excluded.fetched_at
            """,
            (node_id, drives_json, jobs_json, stats_json, fetched_at),
        )
        conn.execute(
            "UPDATE nodes SET online=1,last_seen=?,last_poll=?,last_error=? WHERE id=?",
            (now, now, "; ".join(warnings) if warnings else None, node_id),
        )

        if not jobs_ok:
            return

        for job in jobs_result:
            job_history.upsert_polled_job(conn, node_id, job)
        reported = {str(job.get("id")) for job in jobs_result if job.get("id")}
        job_history.mark_missing_jobs_interrupted(conn, node_id, reported, now)


async def _start_pending(node: sqlite3.Row, drives: List[dict]) -> None:
    """Wait for Disc: launch anything whose disc has now arrived."""
    waiting = intake.for_node(node["id"])
    if not waiting:
        return

    by_name = {str(d.get("name", "")).upper(): d for d in drives}
    auto_eject = db.get_setting_bool("auto_eject", True)

    for row in waiting:
        drive_name = row["drive"]
        if intake.exhausted(row):
            continue
        drive = by_name.get(drive_name)
        if not drive:
            continue
        if drive.get("active_job"):
            continue
        if not node_client.drive_preference(node["id"], drive_name, "detect", True):
            continue
        media_ready = intake.media_is_ready(drive)
        ready_at = await asyncio.to_thread(
            intake.mark_ready, node["id"], drive_name, media_ready
        )
        if not media_ready or ready_at is None:
            continue
        if time.time() - ready_at < intake.AUTO_START_DELAY_SECONDS:
            continue

        request = intake.to_request(row)
        try:
            result = await node_client.post(
                node, f"/drives/{drive_name}/rip", request.node_payload(auto_eject)
            )
        except Exception as exc:  # HTTPException included: the node refused
            detail = getattr(exc, "detail", None) or str(exc)
            log.warning("Auto-start failed on %s/%s: %s", node["id"], drive_name, detail)
            await asyncio.to_thread(intake.record_error, node["id"], drive_name, str(detail))
            continue

        await asyncio.to_thread(
            job_history.record_started_job,
            node["id"], drive_name, request.model_dump(), result, True,
        )
        await asyncio.to_thread(intake.clear, node["id"], drive_name)
        log.info("Auto-started %s on %s: %s", drive_name, node["id"], row["title"])


async def poll_node(node_id: str) -> None:
    node = db.query_one("SELECT * FROM nodes WHERE id=?", (node_id,))
    if not node or not node["enabled"]:
        return

    now = time.time()
    base = node_client.base_url(node)
    headers = node_client.headers_for(node)

    try:
        # Online status depends only on the lightweight health endpoint. Slow
        # media or statistics requests must not hide a healthy node.
        await node_client.get_json(f"{base}/health", headers)
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        with db.write() as conn:
            conn.execute(
                "UPDATE nodes SET online=0,last_poll=?,last_error=? WHERE id=?",
                (now, f"Health check failed: {detail}", node_id),
            )
        await asyncio.to_thread(intake.clear_ready_for_node, node_id)
        return

    with db.write() as conn:
        conn.execute(
            "UPDATE nodes SET online=1,last_seen=?,last_poll=?,last_error=NULL WHERE id=?",
            (now, now, node_id),
        )

    drives_result, jobs_result, stats_result = await node_client.gather_settled(
        node_client.get_json(f"{base}/drives", headers, timeout=node_client.COMMAND_TIMEOUT),
        node_client.get_json(f"{base}/jobs", headers),
        node_client.get_json(f"{base}/system/stats", headers),
    )

    try:
        await asyncio.to_thread(_store_node_results, node_id, now, drives_result, jobs_result, stats_result)
    except sqlite3.Error as exc:
        log.error("Could not store poll results for %s: %s", node_id, exc)
        return

    if isinstance(drives_result, list):
        await _start_pending(node, drives_result)


async def poll_once() -> None:
    ids = [row["id"] for row in db.query("SELECT id FROM nodes WHERE enabled=1")]
    await asyncio.gather(*(poll_node(node_id) for node_id in ids), return_exceptions=True)


def any_active_cached() -> bool:
    for row in db.query("SELECT jobs_json FROM node_cache"):
        try:
            if has_active_jobs(json.loads(row["jobs_json"] or "[]")):
                return True
        except json.JSONDecodeError:
            continue
    return False


def waiting_for_disc() -> bool:
    row = db.query_one("SELECT COUNT(*) AS n FROM pending_intake")
    return bool(row and row["n"])


async def poll_loop() -> None:
    housekeeping = 0.0
    while True:
        try:
            await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Poll cycle failed: %s", exc)

        now = time.time()
        if now - housekeeping > 3600:
            housekeeping = now
            try:
                await asyncio.to_thread(db.prune_history)
            except sqlite3.Error as exc:
                log.warning("History pruning failed: %s", exc)

        # A drive waiting for a disc deserves the fast cadence too, otherwise
        # inserting a disc appears to do nothing for several seconds.
        if any_active_cached() or waiting_for_disc():
            delay = db.get_setting_int("active_poll_seconds", DEFAULT_ACTIVE_POLL)
        else:
            delay = db.get_setting_int("idle_poll_seconds", DEFAULT_IDLE_POLL)

        try:
            wakeup.clear()
            await asyncio.wait_for(wakeup.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


def start() -> None:
    global _task
    _task = asyncio.create_task(poll_loop())


async def stop() -> None:
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
