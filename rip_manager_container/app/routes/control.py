"""Drive controls: rip, wait for disc, retry, eject, close tray, cancel."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

import db
import intake
import jobs as job_history
from models import RipRequest
import nodes as node_client
import poller
import upc

router = APIRouter(tags=["control"])


def _drive(node_id: str, drive_name: str) -> tuple[sqlite3.Row, str]:
    node = node_client.get_node(node_id)
    return node, drive_name.upper()


def _ensure_drive_enabled(node_id: str, drive: str) -> None:
    if not node_client.drive_preference(node_id, drive, "enabled", True):
        raise HTTPException(
            status_code=403,
            detail=f"{drive} is marked Do not use in Settings",
        )


async def _start(node: sqlite3.Row, drive: str, req: RipRequest, auto_started: bool = False) -> dict:
    _ensure_drive_enabled(node["id"], drive)
    auto_eject = db.get_setting_bool("auto_eject", True)
    result = await node_client.post(node, f"/drives/{drive}/rip", req.node_payload(auto_eject))
    job_history.record_started_job(node["id"], drive, req.model_dump(), result, auto_started)
    intake.clear(node["id"], drive)
    poller.wakeup.set()
    return result


# ---------------------------------------------------------------------------
# Start, wait, retry
# ---------------------------------------------------------------------------

@router.post("/nodes/{node_id}/drives/{drive_name}/rip")
async def node_rip(node_id: str, drive_name: str, req: RipRequest):
    node, drive = _drive(node_id, drive_name)
    return await _start(node, drive, req)


@router.post("/drives/{drive_name}/rip")
async def rip(drive_name: str, req: RipRequest):
    node, _ = node_client.find_drive(drive_name)
    return await _start(node, drive_name.upper(), req)


@router.post("/nodes/{node_id}/drives/{drive_name}/intake")
async def wait_for_disc(node_id: str, drive_name: str, req: RipRequest):
    """Label a drive now and rip automatically once a disc is loaded.

    If a readable disc is already sitting in the drive there is nothing to wait
    for, so the rip simply starts.
    """
    node, drive = _drive(node_id, drive_name)
    _ensure_drive_enabled(node_id, drive)
    cached = node_client.find_cached_drive(node_id, drive)
    # The operator may insert a disc and immediately press Start before the
    # background poll refreshes its cache. Ask the node directly so a stale
    # "waiting" snapshot cannot queue the same intake again.
    try:
        cached = await node_client.get_json(
            f"{node_client.base_url(node)}/drives/{drive}",
            node_client.headers_for(node),
            node_client.COMMAND_TIMEOUT,
        )
    except Exception:
        pass

    if cached and cached.get("active_job"):
        raise HTTPException(status_code=409, detail=f"{drive} is already ripping")

    if cached and intake.media_is_ready(cached):
        result = await _start(node, drive, req)
        return {"ok": True, "started": True, "message": f"{drive} started", "result": result}

    intake.queue(node_id, drive, req)

    opened = False
    open_warning = None
    tray = str((cached or {}).get("tray") or ((cached or {}).get("media") or {}).get("tray") or "")
    if tray != "open" and node_client.drive_preference(node_id, drive, "open_tray", True):
        try:
            await node_client.post(node, f"/drives/{drive}/eject")
            opened = True
        except Exception as exc:
            open_warning = getattr(exc, "detail", None) or str(exc)

    poller.wakeup.set()
    return {
        "ok": True,
        "started": False,
        "opened": opened,
        "open_warning": open_warning,
        "message": (
            f"{drive} opened — insert the disc and close the tray"
            if opened else f"{drive} is waiting for a disc"
        ),
        "pending": intake.to_dict(intake.get(node_id, drive)),
    }


@router.delete("/nodes/{node_id}/drives/{drive_name}/intake")
def cancel_wait(node_id: str, drive_name: str):
    node, drive = _drive(node_id, drive_name)
    if not intake.clear(node["id"], drive):
        raise HTTPException(status_code=404, detail=f"{drive} is not waiting for a disc")
    poller.wakeup.set()
    return {"ok": True, "message": f"{drive} is no longer waiting for a disc"}


@router.post("/nodes/{node_id}/drives/{drive_name}/retry")
async def retry(node_id: str, drive_name: str):
    """Run the drive's most recent job again with the same disc details."""
    node, drive = _drive(node_id, drive_name)
    _ensure_drive_enabled(node_id, drive)

    previous = job_history.latest_job_for_drive(node_id, drive)
    if not previous or not previous["title"]:
        raise HTTPException(status_code=404, detail=f"No previous rip is recorded for {drive}")
    if previous["state"] in ("starting", "ripping", "verifying", "cancelling"):
        raise HTTPException(status_code=409, detail=f"{drive} is still working on that rip")

    req = RipRequest(
        title=previous["title"],
        year=previous["year"],
        season=previous["season"],
        disc=previous["disc"],
        barcode=previous["barcode"],
        media_type=previous["media_type"],
        creator=previous["creator"],
        narrator=previous["narrator"],
    )

    cached = node_client.find_cached_drive(node_id, drive)
    if cached and intake.media_is_ready(cached):
        result = await _start(node, drive, req)
        return {"ok": True, "started": True, "message": f"{drive} retrying {req.title}", "result": result}

    # The disc was probably ejected after the failure. Queue it so putting the
    # same disc back in is all that is left to do.
    intake.queue(node_id, drive, req)
    poller.wakeup.set()
    return {
        "ok": True,
        "started": False,
        "message": f"{drive} will retry {req.title} when the disc is back in",
        "pending": intake.to_dict(intake.get(node_id, drive)),
    }


# ---------------------------------------------------------------------------
# Tray and cancel
# ---------------------------------------------------------------------------

@router.post("/nodes/{node_id}/drives/{drive_name}/eject")
async def node_eject(node_id: str, drive_name: str):
    node, drive = _drive(node_id, drive_name)
    if not node_client.drive_preference(node_id, drive, "open_tray", True):
        raise HTTPException(
            status_code=403,
            detail=f"Open/eject commands are switched off for {drive} in Settings",
        )
    result = await node_client.post(node, f"/drives/{drive}/eject")
    poller.wakeup.set()
    return result


@router.post("/nodes/{node_id}/drives/{drive_name}/close")
async def node_close(node_id: str, drive_name: str):
    node, drive = _drive(node_id, drive_name)
    if not node_client.drive_preference(node_id, drive, "close_tray", True):
        raise HTTPException(
            status_code=403,
            detail=f"Close-tray commands are switched off for {drive} in Settings",
        )
    result = await node_client.post(node, f"/drives/{drive}/close")
    poller.wakeup.set()
    return result


@router.post("/nodes/{node_id}/drives/{drive_name}/cancel")
async def node_cancel(node_id: str, drive_name: str):
    node, drive = _drive(node_id, drive_name)
    result = await node_client.post(node, f"/drives/{drive}/cancel")
    poller.wakeup.set()
    return result


@router.post("/nodes/{node_id}/drives/{drive_name}/clear")
async def node_clear(node_id: str, drive_name: str):
    """Clear a finished job card; never delete ripped output files."""
    node, drive = _drive(node_id, drive_name)
    result = await node_client.post(node, f"/drives/{drive}/clear")
    intake.clear(node_id, drive)
    job_history.clear_drive(node_id, drive)
    poller.wakeup.set()
    return {**result, "message": f"{drive} cleared and ready"}


@router.post("/drives/{drive_name}/eject")
async def eject(drive_name: str):
    node, _ = node_client.find_drive(drive_name)
    return await node_eject(node["id"], drive_name)


@router.post("/drives/{drive_name}/close")
async def close(drive_name: str):
    node, _ = node_client.find_drive(drive_name)
    return await node_close(node["id"], drive_name)


@router.post("/drives/{drive_name}/cancel")
async def cancel(drive_name: str):
    node, _ = node_client.find_drive(drive_name)
    return await node_cancel(node["id"], drive_name)


# ---------------------------------------------------------------------------
# Barcode lookup
# ---------------------------------------------------------------------------

@router.get("/lookup/upc/{code}")
async def lookup_upc(code: str):
    if not db.get_setting_bool("upc_lookup", True):
        raise HTTPException(status_code=403, detail="Barcode lookup is switched off in Settings")
    return await upc.lookup(code)
