"""Wait for Disc.

The operator can label a drive before any disc is in it. The details are held
here, one row per drive, and the poller starts the rip the moment media is
detected. This is the piece the V3.4.7 prototype implied with its
"Wait for Disc" button but that no backend ever provided.
"""

from __future__ import annotations

import sqlite3
import time
from typing import List, Optional

import db
from models import RipRequest

MAX_ATTEMPTS = 5
AUTO_START_DELAY_SECONDS = 15


def queue(node_id: str, drive: str, req: RipRequest) -> None:
    now = time.time()
    with db.write() as conn:
        conn.execute(
            """
            INSERT INTO pending_intake(
                node_id, drive, title, year, season, disc, barcode, media_type,
                creator, narrator, created_at, updated_at, attempts, last_error, ready_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL)
            ON CONFLICT(node_id,drive) DO UPDATE SET
                title=excluded.title, year=excluded.year, season=excluded.season,
                disc=excluded.disc, barcode=excluded.barcode, media_type=excluded.media_type,
                creator=excluded.creator, narrator=excluded.narrator,
                updated_at=excluded.updated_at, attempts=0, last_error=NULL,
                ready_at=NULL
            """,
            (node_id, drive.upper(), req.title, req.year, req.season, req.disc,
             req.barcode, req.media_type, req.creator, req.narrator, now, now),
        )


def clear(node_id: str, drive: str) -> bool:
    with db.write() as conn:
        cursor = conn.execute(
            "DELETE FROM pending_intake WHERE node_id=? AND drive=?", (node_id, drive.upper())
        )
    return cursor.rowcount > 0


def get(node_id: str, drive: str) -> Optional[sqlite3.Row]:
    return db.query_one(
        "SELECT * FROM pending_intake WHERE node_id=? AND drive=?", (node_id, drive.upper())
    )


def for_node(node_id: str) -> List[sqlite3.Row]:
    return db.query("SELECT * FROM pending_intake WHERE node_id=?", (node_id,))


def all_pending() -> List[dict]:
    return [to_dict(row) for row in db.query("SELECT * FROM pending_intake ORDER BY created_at")]


def record_error(node_id: str, drive: str, message: str) -> None:
    """Count a failed automatic start so a permanently rejecting drive gives up
    instead of hammering the node on every poll."""
    with db.write() as conn:
        conn.execute(
            "UPDATE pending_intake SET attempts=attempts+1, last_error=?, updated_at=? "
            "WHERE node_id=? AND drive=?",
            (message[:500], time.time(), node_id, drive.upper()),
        )


def mark_ready(node_id: str, drive: str, ready: bool) -> Optional[float]:
    """Start or cancel the silent delay after readable media is detected."""
    now = time.time()
    with db.write() as conn:
        row = conn.execute(
            "SELECT ready_at FROM pending_intake WHERE node_id=? AND drive=?",
            (node_id, drive.upper()),
        ).fetchone()
        if not row:
            return None
        if not ready:
            if row["ready_at"] is not None:
                conn.execute(
                    "UPDATE pending_intake SET ready_at=NULL, updated_at=? "
                    "WHERE node_id=? AND drive=?",
                    (now, node_id, drive.upper()),
                )
            return None
        if row["ready_at"] is None:
            conn.execute(
                "UPDATE pending_intake SET ready_at=?, updated_at=? "
                "WHERE node_id=? AND drive=?",
                (now, now, node_id, drive.upper()),
            )
            return now
        return float(row["ready_at"])


def exhausted(row: sqlite3.Row) -> bool:
    return int(row["attempts"] or 0) >= MAX_ATTEMPTS


def to_request(row: sqlite3.Row) -> RipRequest:
    return RipRequest(
        title=row["title"],
        year=row["year"],
        season=row["season"],
        disc=row["disc"],
        barcode=row["barcode"],
        media_type=row["media_type"],
        creator=row["creator"],
        narrator=row["narrator"],
    )


def to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["exhausted"] = exhausted(row)
    return item


def media_is_ready(drive: dict) -> bool:
    """True when a disc is loaded and readable enough to attempt a rip.

    Rip Node 0.1.16 reports a tray state, which is the reliable signal. Older
    nodes only say whether media is present, so that is used as the fallback.
    """
    media = drive.get("media") or {}
    tray = drive.get("tray") or media.get("tray")
    if tray in {"disc", "open", "empty", "loading"}:
        return tray == "disc"
    # "unknown" is not proof that the drive is empty. Some USB optical
    # bridges cannot report tray state but do correctly report media.present.
    return bool(media.get("present")) and media.get("reason") != "being_ripped"
