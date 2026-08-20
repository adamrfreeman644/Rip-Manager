"""Settings read and write."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Response

import auth
from config import DEFAULT_ACTIVE_POLL, DEFAULT_IDLE_POLL
import db
from models import SettingsUpdate
import poller

router = APIRouter(tags=["settings"])


def current_settings() -> dict:
    configured = [dict(row) for row in db.query("SELECT id,name,url,enabled FROM nodes ORDER BY id")]
    for node in configured:
        node["enabled"] = bool(node["enabled"])
    return {
        "idle_poll_seconds": db.get_setting_int("idle_poll_seconds", DEFAULT_IDLE_POLL),
        "active_poll_seconds": db.get_setting_int("active_poll_seconds", DEFAULT_ACTIVE_POLL),
        "theme": db.get_setting("theme", "dark"),
        "sounds": db.get_setting_bool("sounds", True),
        "volume": db.get_setting_int("volume", 75),
        "auto_eject": db.get_setting_bool("auto_eject", True),
        "confirm_eject": db.get_setting_bool("confirm_eject", True),
        "upc_lookup": db.get_setting_bool("upc_lookup", True),
        "metadata_musicbrainz": db.get_setting_bool("metadata_musicbrainz", True),
        "metadata_google_books": db.get_setting_bool("metadata_google_books", True),
        "metadata_google_books_key": db.get_setting("metadata_google_books_key", ""),
        "metadata_upcitemdb": db.get_setting_bool("metadata_upcitemdb", True),
        "metadata_upcitemdb_mode": db.get_setting("metadata_upcitemdb_mode", "free"),
        "metadata_upcitemdb_key": db.get_setting("metadata_upcitemdb_key", ""),
        "metadata_omdb": db.get_setting_bool("metadata_omdb", True),
        "metadata_omdb_key": db.get_setting("metadata_omdb_key", ""),
        "minimum_video_minutes": db.get_setting_int("minimum_video_minutes", 2),
        "verify_before_eject": db.get_setting_bool("verify_before_eject", True),
        "prefer_english_audio": db.get_setting_bool("prefer_english_audio", True),
        "prefer_english_subtitles": db.get_setting_bool("prefer_english_subtitles", True),
        "drive_preferences": db.get_setting_json("drive_preferences", {}),
        "dashboard_columns": db.get_setting_int("dashboard_columns", 3),
        "dashboard_rows": db.get_setting_int("dashboard_rows", 2),
        "dashboard_tiles": db.get_setting_json("dashboard_tiles", []),
        "dashboard_spacing_percent": db.get_setting_int("dashboard_spacing_percent", 100),
        "simulation": db.get_setting_bool("simulation"),
        "lock_enabled": db.get_setting_bool("lock_enabled"),
        "pin_set": auth.pin_is_set(),
        "nodes": configured,
    }


@router.get("/settings")
def get_settings():
    return current_settings()


@router.put("/settings")
def update_settings(req: SettingsUpdate, response: Response):
    # First-time PIN creation needs no old PIN. Once a PIN exists, changing it
    # requires that existing PIN to be supplied and verified server-side.
    if req.new_pin and auth.pin_is_set():
        encoded = db.get_setting("pin_hash", "")
        if not req.current_pin or not auth.verify_pin(req.current_pin, encoded):
            raise HTTPException(status_code=403, detail="Current PIN is incorrect")
    if req.lock_enabled is True and not auth.pin_is_set() and not req.new_pin:
        raise HTTPException(status_code=422, detail="Set a 4–8 digit PIN before enabling the lock")
    if req.nodes is not None and len({n.id for n in req.nodes}) != len(req.nodes):
        raise HTTPException(status_code=422, detail="Each node can only appear once")
    if req.dashboard_tiles is not None:
        cols = req.dashboard_columns or db.get_setting_int("dashboard_columns", 3)
        rows = req.dashboard_rows or db.get_setting_int("dashboard_rows", 2)
        if len(req.dashboard_tiles) > cols * rows:
            raise HTTPException(status_code=422, detail="Dashboard has more tile assignments than grid cells")
        assigned = [item for item in req.dashboard_tiles if item]
        if len(assigned) != len(set(assigned)):
            raise HTTPException(status_code=422, detail="A drive can only be assigned to one dashboard cell")

    db.set_settings({
        "idle_poll_seconds": req.idle_poll_seconds,
        "active_poll_seconds": req.active_poll_seconds,
        "theme": req.theme,
        "sounds": None if req.sounds is None else int(req.sounds),
        "volume": req.volume,
        "auto_eject": None if req.auto_eject is None else int(req.auto_eject),
        "confirm_eject": None if req.confirm_eject is None else int(req.confirm_eject),
        "upc_lookup": None if req.upc_lookup is None else int(req.upc_lookup),
        "metadata_musicbrainz": None if req.metadata_musicbrainz is None else int(req.metadata_musicbrainz),
        "metadata_google_books": None if req.metadata_google_books is None else int(req.metadata_google_books),
        "metadata_google_books_key": req.metadata_google_books_key,
        "metadata_upcitemdb": None if req.metadata_upcitemdb is None else int(req.metadata_upcitemdb),
        "metadata_upcitemdb_mode": req.metadata_upcitemdb_mode,
        "metadata_upcitemdb_key": req.metadata_upcitemdb_key,
        "metadata_omdb": None if req.metadata_omdb is None else int(req.metadata_omdb),
        "metadata_omdb_key": req.metadata_omdb_key,
        "minimum_video_minutes": req.minimum_video_minutes,
        "verify_before_eject": None if req.verify_before_eject is None else int(req.verify_before_eject),
        "prefer_english_audio": None if req.prefer_english_audio is None else int(req.prefer_english_audio),
        "prefer_english_subtitles": None if req.prefer_english_subtitles is None else int(req.prefer_english_subtitles),
        "simulation": None if req.simulation is None else int(req.simulation),
        "lock_enabled": None if req.lock_enabled is None else int(req.lock_enabled),
        "drive_preferences": (
            json.dumps(req.drive_preferences, separators=(",", ":"))
            if req.drive_preferences is not None else None
        ),
        "dashboard_columns": req.dashboard_columns,
        "dashboard_rows": req.dashboard_rows,
        "dashboard_tiles": (
            json.dumps(req.dashboard_tiles, separators=(",", ":"))
            if req.dashboard_tiles is not None else None
        ),
        "dashboard_spacing_percent": req.dashboard_spacing_percent,
    })

    if req.new_pin:
        db.set_setting("pin_hash", auth.hash_pin(req.new_pin))
        # Changing the PIN must invalidate every existing session.
        auth.revoke_all_sessions()

    if req.nodes is not None:
        known = {row["id"] for row in db.query("SELECT id FROM nodes")}
        unknown = [n.id for n in req.nodes if n.id not in known]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown node: {unknown[0]}")
        with db.write() as conn:
            for node in req.nodes:
                conn.execute(
                    "UPDATE nodes SET name=?,url=?,enabled=? WHERE id=?",
                    (node.name, node.url.rstrip("/"), int(node.enabled), node.id),
                )

    if req.new_pin:
        auth.issue_session(response)

    poller.wakeup.set()
    return current_settings()
