"""Read-only fleet views and node configuration."""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, HTTPException, Query

import db
import intake
import jobs as job_history
from models import NodeCreate, NodeDeleteRequest, NodeUpdate
import poller
from routes.settings import current_settings

router = APIRouter(tags=["fleet"])


def _decode(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


@router.get("/nodes")
def list_nodes():
    rows = db.query(
        "SELECT id,name,url,enabled,last_seen,online,last_error,last_poll FROM nodes ORDER BY id"
    )
    return [dict(row) for row in rows]


@router.post("/nodes")
def add_node(req: NodeCreate):
    try:
        with db.write() as conn:
            conn.execute(
                "INSERT INTO nodes(id,name,url,enabled,token) VALUES (?,?,?,?,?)",
                (req.id, req.name, req.url.rstrip("/"), 1 if req.enabled else 0, req.token),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Node ID already exists") from exc
    poller.wakeup.set()
    return {"ok": True, "id": req.id}


@router.put("/nodes/{node_id}")
def update_node(node_id: str, req: NodeUpdate):
    row = db.query_one("SELECT * FROM nodes WHERE id=?", (node_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown node")
    with db.write() as conn:
        conn.execute(
            "UPDATE nodes SET name=?,url=?,enabled=?,token=? WHERE id=?",
            (
                req.name if req.name is not None else row["name"],
                req.url.rstrip("/") if req.url is not None else row["url"],
                int(req.enabled if req.enabled is not None else bool(row["enabled"])),
                req.token if req.token is not None else row["token"],
                node_id,
            ),
        )
    poller.wakeup.set()
    return {"ok": True}


@router.delete("/nodes/{node_id}")
def delete_node(node_id: str, req: NodeDeleteRequest):
    row = db.query_one("SELECT id,name,url FROM nodes WHERE id=?", (node_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown node")
    if row["id"] == "simulator" or "simulator-node" in row["url"]:
        raise HTTPException(status_code=409, detail="The built-in Simulator cannot be removed")
    if req.confirm_name != row["name"]:
        raise HTTPException(status_code=422, detail="Type the node's friendly name exactly to confirm removal")

    cached = db.query_one("SELECT jobs_json FROM node_cache WHERE node_id=?", (node_id,))
    if cached and poller.has_active_jobs(_decode(cached["jobs_json"], [])):
        raise HTTPException(status_code=409, detail="This node has an active rip. Wait for it to finish or cancel it before removing the node")

    dashboard_tiles = db.get_setting_json("dashboard_tiles", [])
    cleaned_tiles = [None if value and str(value).startswith(f"{node_id}:") else value for value in dashboard_tiles]
    drive_preferences = db.get_setting_json("drive_preferences", {})
    cleaned_preferences = {
        key: value for key, value in drive_preferences.items()
        if not str(key).startswith(f"{node_id}:")
    }
    with db.write() as conn:
        conn.execute("DELETE FROM node_cache WHERE node_id=?", (node_id,))
        conn.execute("DELETE FROM pending_intake WHERE node_id=?", (node_id,))
        deleted = conn.execute("DELETE FROM nodes WHERE id=?", (node_id,)).rowcount
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('dashboard_tiles',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(cleaned_tiles),),
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('drive_preferences',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(cleaned_preferences),),
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Unknown node")
    db.refresh_settings_cache()
    poller.wakeup.set()
    return {"ok": True, "removed": node_id, "history_preserved": True}


@router.post("/poll-now")
async def poll_now():
    await poller.poll_once()
    return {"ok": True}


@router.get("/overview")
def overview():
    nodes = db.query(
        "SELECT id,name,url,enabled,last_seen,online,last_error,last_poll FROM nodes ORDER BY id"
    )
    cache = {row["node_id"]: row for row in db.query("SELECT * FROM node_cache")}
    payload = []
    for node in nodes:
        cached = cache.get(node["id"])
        payload.append({
            **dict(node),
            "drives": _decode(cached["drives_json"], []) if cached else [],
            "jobs": _decode(cached["jobs_json"], []) if cached else [],
            "stats": _decode(cached["stats_json"], {}) if cached else {},
            "fetched_at": cached["fetched_at"] if cached else None,
        })
    return {"nodes": payload, "settings": current_settings(), "pending_intake": intake.all_pending()}


@router.get("/drives")
def drives():
    """Every drive on every enabled node, flattened for the tile grid."""
    nodes = db.query("SELECT id,name,online FROM nodes WHERE enabled=1 ORDER BY id")
    cache = {
        row["node_id"]: row
        for row in db.query("SELECT node_id,drives_json,fetched_at FROM node_cache")
    }
    waiting = {(row["node_id"], row["drive"]): intake.to_dict(row) for row in db.query("SELECT * FROM pending_intake")}

    out = []
    for node in nodes:
        cached = cache.get(node["id"])
        if not cached:
            continue
        for drive in _decode(cached["drives_json"], []):
            name = str(drive.get("name", "")).upper()
            out.append({
                **drive,
                "node_id": node["id"],
                "node_name": node["name"],
                "node_online": bool(node["online"]),
                "fetched_at": cached["fetched_at"],
                "manager_drive_id": f"{node['id']}:{name}",
                "pending_intake": waiting.get((node["id"], name)),
            })
    return out


@router.get("/intake")
def pending_intake():
    return intake.all_pending()


@router.get("/jobs")
def list_jobs(limit: int = Query(default=200, ge=1, le=1000)):
    rows = db.query(
        "SELECT * FROM jobs_history WHERE cleared=0 ORDER BY COALESCE(started_at,updated_at) DESC LIMIT ?",
        (limit,),
    )
    return [job_history.row_to_dict(row) for row in rows]


@router.get("/events")
def list_events(since_id: int = 0, limit: int = Query(default=100, ge=1, le=500)):
    rows = db.query("SELECT * FROM events WHERE id>? ORDER BY id ASC LIMIT ?", (since_id, limit))
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = _decode(item.pop("payload_json"), {})
        out.append(item)
    return out


@router.get("/system/stats")
def system_stats():
    nodes = db.query("SELECT id,name,online FROM nodes WHERE enabled=1 ORDER BY id")
    cache = {
        row["node_id"]: row
        for row in db.query("SELECT node_id,stats_json,fetched_at FROM node_cache")
    }
    return [{
        "node_id": node["id"],
        "node_name": node["name"],
        "online": bool(node["online"]),
        "fetched_at": cache[node["id"]]["fetched_at"] if node["id"] in cache else None,
        "stats": _decode(cache[node["id"]]["stats_json"], {}) if node["id"] in cache else {},
    } for node in nodes]
