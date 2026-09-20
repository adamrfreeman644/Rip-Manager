"""Persistent, single-folder transfer queue for completed rips."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

import archive
import db
import media_prep

log = logging.getLogger("rip-manager.mover")
_task: Optional[asyncio.Task] = None
_wakeup: Optional[asyncio.Event] = None


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_paths(job: dict) -> tuple[Path, Path]:
    node_id = job["node_id"]
    mounts = db.get_setting_json("mover_node_mounts", {})
    source_roots = db.get_setting_json("mover_node_source_roots", {})
    configured_mount = mounts.get(node_id)
    output = Path(job.get("output_dir") or "")
    if not configured_mount or not output.is_absolute():
        raise ValueError(f"No mounted rip-share path is configured for {node_id}")
    mount = Path(configured_mount).resolve()
    node_root = Path(source_roots.get(node_id, "/mnt/ripping"))
    try:
        relative = output.relative_to(node_root)
    except ValueError as exc:
        raise ValueError(f"Node output is outside its configured rip root: {output}") from exc
    source = (mount / relative).resolve()
    if not _under(source, mount) or source == mount:
        raise ValueError("Unsafe source path")
    destination_root = Path(db.get_setting("mover_destination_root", "/media")).resolve()
    folders = db.get_setting_json("mover_destination_folders", {})
    media_folder = folders.get(job.get("media_type"), "Other")
    destination_parent = (destination_root / media_folder).resolve()
    if not _under(destination_parent, destination_root):
        raise ValueError("Unsafe destination folder setting")
    return source, destination_parent / source.name


def enqueue(manager_job_id: str) -> bool:
    """Queue only the completion event that called us; never sweep old history."""
    if not db.get_setting_bool("mover_enabled"):
        return False
    row = db.query_one("SELECT state FROM jobs_history WHERE manager_job_id=?", (manager_job_id,))
    if not row or row["state"] != "complete":
        return False
    now = time.time()
    with db.write() as conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO transfer_queue(manager_job_id,state,created_at,updated_at) VALUES (?,'queued',?,?)",
            (manager_job_id, now, now),
        ).rowcount
    wake()
    return bool(inserted)


def wake() -> None:
    if _wakeup is not None:
        _wakeup.set()


def _set(transfer_id: int, **values) -> None:
    if not values:
        return
    values["updated_at"] = time.time()
    fields = ",".join(f"{key}=?" for key in values)
    with db.write() as conn:
        conn.execute(f"UPDATE transfer_queue SET {fields} WHERE id=?", (*values.values(), transfer_id))


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(source: Path) -> list[tuple[Path, Path, int]]:
    if not source.is_dir():
        raise FileNotFoundError(f"Source folder is unavailable: {source}")
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not moved: {path.name}")
        if path.is_file():
            files.append((path, path.relative_to(source), path.stat().st_size))
    if not files:
        raise ValueError("Source folder is empty")
    return files


def _copy_transfer(transfer: dict, job: dict) -> None:
    transfer_id = transfer["id"]
    source, destination = _resolve_paths(job)
    partial = destination.with_name(f".{destination.name}.partial")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    # The generated text sidecar is the completion marker for preparation.
    # It is written only for the exact completed job being transferred.
    archive.sync_sidecars(job["manager_job_id"], source)
    if not (source / "disc-info.txt").is_file():
        raise ValueError("Completed-rip marker could not be written")
    prep = media_prep.prepare_completed_rip(source, job)
    log.info("Prepared %s before mover transfer: %s", job["manager_job_id"], prep)
    files = _inventory(source)
    total = sum(item[2] for item in files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir()
    _set(transfer_id, state="copying", source_dir=str(source), destination_dir=str(destination),
         bytes_total=total, bytes_copied=0, files_total=len(files), files_copied=0,
         error=None, started_at=time.time())
    copied = 0
    for number, (source_file, relative, size) in enumerate(files, 1):
        target = partial / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source_hash = hashlib.sha256()
        with source_file.open("rb") as src, target.open("wb") as dst:
            while True:
                chunk = src.read(4 * 1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                source_hash.update(chunk)
                copied += len(chunk)
                _set(transfer_id, bytes_copied=copied)
            dst.flush()
            os.fsync(dst.fileno())
        shutil.copystat(source_file, target)
        if target.stat().st_size != size or _hash(target) != source_hash.hexdigest():
            raise IOError(f"Verification failed for {relative}")
        _set(transfer_id, files_copied=number)
    _set(transfer_id, state="verifying")
    os.replace(partial, destination)
    warning = None
    try:
        archive.set_final_dir(job["manager_job_id"], destination)
    except OSError as exc:
        warning = f"Media copied and verified, but archive sidecars need attention: {exc}"
    source_removed = False
    if not warning and db.get_setting_bool("mover_delete_source", True):
        try:
            shutil.rmtree(source)
            source_removed = True
        except OSError as exc:
            warning = f"Media copied and verified; source cleanup failed: {exc}"
    if not warning:
        archive.record_file_changes(destination, [
            *[{"path": relative.as_posix(), "action": "moved_and_verified", "size_bytes": size}
              for _, relative, size in files],
            {"path": str(source), "action": "source_removed"} if source_removed else
            {"path": str(source), "action": "source_retained"},
        ])
    _set(transfer_id, state="complete", bytes_copied=total, files_copied=len(files),
         error=warning, finished_at=time.time())


def _next() -> Optional[dict]:
    row = db.query_one("SELECT * FROM transfer_queue WHERE state='queued' ORDER BY created_at,id LIMIT 1")
    return dict(row) if row else None


def list_transfers() -> list[dict]:
    rows = db.query(
        """SELECT q.*,j.title,j.year,j.media_type,j.node_id
           FROM transfer_queue q JOIN jobs_history j USING(manager_job_id)
           ORDER BY CASE q.state WHEN 'copying' THEN 0 WHEN 'verifying' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                    q.created_at"""
    )
    return [dict(row) for row in rows]


def retry(transfer_id: int) -> None:
    row = db.query_one("SELECT state FROM transfer_queue WHERE id=?", (transfer_id,))
    if not row:
        raise KeyError(transfer_id)
    if row["state"] not in {"failed", "cancelled"}:
        raise ValueError("Only failed or cancelled transfers can be retried")
    _set(transfer_id, state="queued", error=None, bytes_copied=0, files_copied=0,
         started_at=None, finished_at=None)
    wake()


def cancel(transfer_id: int) -> None:
    row = db.query_one("SELECT state,destination_dir FROM transfer_queue WHERE id=?", (transfer_id,))
    if not row:
        raise KeyError(transfer_id)
    if row["state"] != "queued":
        raise ValueError("Only a queued transfer can be cancelled safely")
    _set(transfer_id, state="cancelled", finished_at=time.time())


async def _loop() -> None:
    global _wakeup
    _wakeup = asyncio.Event()
    while True:
        transfer = _next()
        if transfer:
            job_row = db.query_one("SELECT * FROM jobs_history WHERE manager_job_id=?", (transfer["manager_job_id"],))
            if not job_row:
                _set(transfer["id"], state="failed", error="Rip history record is missing", finished_at=time.time())
                continue
            import jobs
            job = jobs.row_to_dict(job_row)
            try:
                await asyncio.to_thread(_copy_transfer, transfer, job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Transfer %s failed", transfer["id"])
                _set(transfer["id"], state="failed", error=str(exc), finished_at=time.time())
            continue
        try:
            _wakeup.clear()
            await asyncio.wait_for(_wakeup.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


def start() -> None:
    global _task
    # An interrupted copy is safe to retry: the worker recreates only its own
    # hidden .partial directory and the source was never deleted.
    with db.write() as conn:
        conn.execute(
            "UPDATE transfer_queue SET state='queued',error='Manager restarted; transfer queued again',updated_at=? "
            "WHERE state IN ('copying','verifying')", (time.time(),)
        )
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
