"""Physical-media library, photo, sidecar and mover endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import archive
import db
import mover

router = APIRouter(prefix="/archive", tags=["physical media"])


class TextUpdate(BaseModel):
    text: str = Field(max_length=262144)


class ImageUpdate(BaseModel):
    data_url: str


class MoverConfig(BaseModel):
    enabled: bool
    delete_source: bool = True
    destination_root: str = Field(min_length=1, max_length=500)
    node_mounts: Dict[str, str] = Field(default_factory=dict)
    node_source_roots: Dict[str, str] = Field(default_factory=dict)
    destination_folders: Dict[str, str] = Field(default_factory=dict)


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
    paths = [req.destination_root, *req.node_mounts.values(), *req.node_source_roots.values()]
    if any(not Path(value).is_absolute() for value in paths):
        raise HTTPException(status_code=422, detail="Mover paths must be absolute")
    allowed_types = {"movie", "tv", "music", "audiobook"}
    if set(req.destination_folders) - allowed_types:
        raise HTTPException(status_code=422, detail="Unknown destination media type")
    if set(req.destination_folders) != allowed_types or any(not value.strip() for value in req.destination_folders.values()):
        raise HTTPException(status_code=422, detail="Set a folder for movies, TV, music and audiobooks")
    if any(Path(value).is_absolute() or ".." in Path(value).parts for value in req.destination_folders.values()):
        raise HTTPException(status_code=422, detail="Destination folders must be safe relative paths")
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
