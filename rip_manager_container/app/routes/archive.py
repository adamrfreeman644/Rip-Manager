"""Physical-media library, photo, sidecar and mover endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import archive
import db
import mover
from routes import node_settings

router = APIRouter(prefix="/archive", tags=["physical media"])


class TextUpdate(BaseModel):
    text: str = Field(max_length=262144)


class ImageUpdate(BaseModel):
    data_url: str


class BarcodeUpdate(BaseModel):
    barcode: Optional[str] = Field(default=None, max_length=32)


class MoverConfig(BaseModel):
    enabled: bool
    delete_source: bool = True
    destination_root: str = Field(min_length=1, max_length=500)
    node_mounts: Dict[str, str] = Field(default_factory=dict)
    node_source_roots: Dict[str, str] = Field(default_factory=dict)
    destination_folders: Dict[str, str] = Field(default_factory=dict)


def _check_path(value: str, write: bool = False) -> dict:
    path = Path(value)
    result = {"path": value, "exists": path.is_dir(), "readable": False, "writable": False}
    if not result["exists"]:
        result["message"] = "Folder is not mounted inside Rip Manager"
        return result
    result["readable"] = os.access(path, os.R_OK | os.X_OK)
    result["writable"] = os.access(path, os.W_OK | os.X_OK)
    if not result["readable"]:
        result["message"] = "Folder exists but Rip Manager cannot read it"
    elif write and not result["writable"]:
        result["message"] = "Folder is readable but not writable"
    else:
        result["message"] = "Ready"
    return result


def _validate_mover_paths(req: MoverConfig) -> None:
    paths = [req.destination_root, *req.node_mounts.values(), *req.node_source_roots.values()]
    if any(not Path(value).is_absolute() for value in paths):
        raise HTTPException(status_code=422, detail="Mover paths must be absolute")
    allowed_types = {"movie", "tv", "music", "audiobook"}
    if set(req.destination_folders) != allowed_types or any(not value.strip() for value in req.destination_folders.values()):
        raise HTTPException(status_code=422, detail="Set a folder for movies, TV, music and audiobooks")
    if any(Path(value).is_absolute() or ".." in Path(value).parts for value in req.destination_folders.values()):
        raise HTTPException(status_code=422, detail="Destination folders must be safe relative paths")


def _not_found(exc: Exception):
    raise HTTPException(status_code=404, detail="Unknown rip record") from exc


@router.get("/media")
def media_list():
    return archive.list_media()


@router.post("/scan")
def scan_existing_media():
    try:
        return archive.scan_existing()
    except OSError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/storage/setup")
async def verify_storage_and_build_library(req: MoverConfig):
    """Save, detect and verify storage, then safely index the reachable library."""
    _validate_mover_paths(req)
    source_roots = dict(req.node_source_roots)
    node_results = []
    configured = db.query("SELECT id,name,url FROM nodes WHERE enabled=1 ORDER BY id")
    for row in configured:
        node = dict(row)
        if node["id"] == "simulator" or "/simulator-node" in node["url"]:
            continue
        detected = None
        error = None
        try:
            storage = await node_settings._storage_request(node["id"], "GET")
            detected = storage.get("path")
            if detected and Path(detected).is_absolute():
                source_roots[node["id"]] = detected
        except HTTPException as exc:
            error = str(exc.detail)
        mount = req.node_mounts.get(node["id"], f"/rip-nodes/{node['id']}")
        check = _check_path(mount, write=True)
        node_results.append({
            "id": node["id"], "name": node["name"], "mount": check,
            "source_root": source_roots.get(node["id"], "/mnt/ripping"),
            "detected": bool(detected), "node_error": error,
        })

    db.set_settings({
        "mover_enabled": int(req.enabled),
        "mover_delete_source": int(req.delete_source),
        "mover_destination_root": req.destination_root,
        "mover_node_mounts": json.dumps(req.node_mounts, separators=(",", ":")),
        "mover_node_source_roots": json.dumps(source_roots, separators=(",", ":")),
        "mover_destination_folders": json.dumps(req.destination_folders, separators=(",", ":")),
    })
    destination = _check_path(req.destination_root, write=True)
    scan = archive.scan_existing() if destination["readable"] else {"ok": False, "folders_seen": 0, "imported": 0}
    ready = destination["exists"] and destination["readable"] and destination["writable"]
    ready = ready and all(item["mount"]["exists"] and item["mount"]["readable"] for item in node_results)
    mover.wake()
    return {"ok": ready, "destination": destination, "nodes": node_results,
            "source_roots": source_roots, "scan": scan}


@router.get("/{manager_job_id}/text")
def get_text(manager_job_id: str):
    try:
        return {"manager_job_id": manager_job_id, "text": archive.text_for(manager_job_id)}
    except KeyError as exc:
        _not_found(exc)


@router.put("/{manager_job_id}/text")
def put_text(manager_job_id: str, req: TextUpdate):
    try:
        return {"ok": True, "text": archive.save_text(manager_job_id, req.text)}
    except KeyError as exc:
        _not_found(exc)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{manager_job_id}/barcode")
def put_barcode(manager_job_id: str, req: BarcodeUpdate):
    try:
        return {"ok": True, "barcode": archive.save_barcode(manager_job_id, req.barcode)}
    except KeyError as exc:
        _not_found(exc)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{manager_job_id}/images/{filename}")
def get_image(manager_job_id: str, filename: str):
    try:
        return FileResponse(archive.image_path(manager_job_id, filename))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc


@router.put("/{manager_job_id}/images/{slot}")
def put_image(manager_job_id: str, slot: str, req: ImageUpdate):
    try:
        filename = archive.save_image(manager_job_id, slot, req.data_url)
        return {"ok": True, "filename": filename}
    except KeyError as exc:
        _not_found(exc)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{manager_job_id}/images/{slot}")
def remove_image(manager_job_id: str, slot: str, index: Optional[int] = None):
    try:
        archive.delete_image(manager_job_id, slot, index)
        return {"ok": True}
    except KeyError as exc:
        _not_found(exc)
    except (IndexError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/mover/config")
def mover_config():
    return {
        "enabled": db.get_setting_bool("mover_enabled"),
        "delete_source": db.get_setting_bool("mover_delete_source", True),
        "destination_root": db.get_setting("mover_destination_root", "/media"),
        "node_mounts": db.get_setting_json("mover_node_mounts", {}),
        "node_source_roots": db.get_setting_json("mover_node_source_roots", {}),
        "destination_folders": db.get_setting_json("mover_destination_folders", {}),
    }


@router.put("/mover/config")
def save_mover_config(req: MoverConfig):
    _validate_mover_paths(req)
    db.set_settings({
        "mover_enabled": int(req.enabled),
        "mover_delete_source": int(req.delete_source),
        "mover_destination_root": req.destination_root,
        "mover_node_mounts": __import__("json").dumps(req.node_mounts, separators=(",", ":")),
        "mover_node_source_roots": __import__("json").dumps(req.node_source_roots, separators=(",", ":")),
        "mover_destination_folders": __import__("json").dumps(req.destination_folders, separators=(",", ":")),
    })
    mover.wake()
    return mover_config()


@router.get("/mover/transfers")
def transfers():
    return mover.list_transfers()


@router.post("/mover/enqueue/{manager_job_id}")
def enqueue_transfer(manager_job_id: str):
    try:
        queued = mover.enqueue(manager_job_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not queued:
        raise HTTPException(status_code=409, detail="Mover is disabled, rip is incomplete, or transfer already exists")
    return {"ok": True}


@router.post("/mover/transfers/{transfer_id}/retry")
def retry_transfer(transfer_id: int):
    try:
        mover.retry(transfer_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Transfer not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/mover/transfers/{transfer_id}/cancel")
def cancel_transfer(transfer_id: int):
    try:
        mover.cancel(transfer_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Transfer not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
