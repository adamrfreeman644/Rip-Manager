"""Physical-disc archive records and sidecar files.

User text is kept outside a replaceable auto-generated block. This means a
later mover or HandBrake integration can refresh technical facts without
destroying anything the owner typed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import db
from config import PHYSICAL_MEDIA_DIR, VERSION

AUTO_START = "--- RIP MANAGER AUTO INFO START ---"
AUTO_END = "--- RIP MANAGER AUTO INFO END ---"
IMAGE_NAMES = {"front": "physical-cover-front", "rear": "physical-cover-rear"}
IMAGE_TYPES = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/heic": ".heic", "image/heif": ".heif",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _record_dir(manager_job_id: str) -> Path:
    key = hashlib.sha256(manager_job_id.encode("utf-8")).hexdigest()[:24]
    path = PHYSICAL_MEDIA_DIR / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_record(manager_job_id: str) -> None:
    now = time.time()
    with db.write() as conn:
        exists = conn.execute(
            "SELECT 1 FROM jobs_history WHERE manager_job_id=?", (manager_job_id,)
        ).fetchone()
        if not exists:
            raise KeyError(manager_job_id)
        conn.execute(
            "INSERT OR IGNORE INTO physical_media(manager_job_id,created_at,updated_at) VALUES (?,?,?)",
            (manager_job_id, now, now),
        )


def _auto_text(job: dict, final_dir: Optional[str] = None) -> str:
    verification = job.get("verification") or {}
    raw = job.get("raw") or {}
    lines = [
        AUTO_START,
        f"TITLE: {job.get('title') or 'Unknown'}",
        f"YEAR: {job.get('year') or ''}",
        f"UPC / EAN: {job.get('barcode') or ''}",
        f"MEDIA TYPE: {job.get('media_type') or 'unknown'}",
        "",
        "DISC",
        f"Node: {job.get('node_id') or ''}",
        f"Drive: {job.get('drive') or ''}",
        f"Rip started: {job.get('started_at') or ''}",
        f"Rip finished: {job.get('finished_at') or ''}",
        f"Rip status: {job.get('state') or ''}",
        f"Verification: {'Passed' if verification.get('ok') else 'Not passed'}",
        f"Files checked: {verification.get('files_checked', '')}",
        f"Original output: {job.get('output_dir') or ''}",
        f"Current location: {final_dir or job.get('output_dir') or ''}",
        f"Output size: {raw.get('output_bytes') or ''} bytes",
        "",
        "SOFTWARE",
        f"Rip Manager: {VERSION}",
        AUTO_END,
    ]
    return "\n".join(lines).rstrip() + "\n"


def merge_auto_text(user_text: str, auto_text: str) -> str:
    """Replace only our delimited block, preserving every user-owned byte."""
    pattern = re.compile(re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END) + r"\n?", re.S)
    clean = pattern.sub("", user_text or "").rstrip()
    if not clean:
        clean = "NOTES\nAdd physical-edition notes here."
    return f"{clean}\n\n{auto_text}"


def _job(manager_job_id: str) -> dict:
    import jobs
    row = db.query_one("SELECT * FROM jobs_history WHERE manager_job_id=?", (manager_job_id,))
    if not row:
        raise KeyError(manager_job_id)
    return jobs.row_to_dict(row)


def text_for(manager_job_id: str) -> str:
    _ensure_record(manager_job_id)
    job = _job(manager_job_id)
    row = db.query_one("SELECT user_text,final_dir FROM physical_media WHERE manager_job_id=?", (manager_job_id,))
    return merge_auto_text(row["user_text"], _auto_text(job, row["final_dir"]))


def save_text(manager_job_id: str, text: str) -> str:
    if len(text.encode("utf-8")) > 256 * 1024:
        raise ValueError("disc-info.txt is too large")
    _ensure_record(manager_job_id)
    # Store user content with the generated block stripped. It is reattached on read.
    user_text = re.sub(
        re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END) + r"\n?", "", text, flags=re.S
    ).rstrip()
    with db.write() as conn:
        conn.execute(
            "UPDATE physical_media SET user_text=?,updated_at=? WHERE manager_job_id=?",
            (user_text, time.time(), manager_job_id),
        )
    sync_sidecars(manager_job_id)
    return text_for(manager_job_id)


def save_image(manager_job_id: str, slot: str, data_url: str) -> str:
    _ensure_record(manager_job_id)
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", data_url, re.S)
    if not match or match.group(1).lower() not in IMAGE_TYPES:
        raise ValueError("Use a JPEG, PNG, WebP, HEIC or HEIF image")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise ValueError("Invalid image upload") from exc
    if not payload or len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be between 1 byte and 20 MB")
    ext = IMAGE_TYPES[match.group(1).lower()]
    record_dir = _record_dir(manager_job_id)
    if slot in IMAGE_NAMES:
        stem = IMAGE_NAMES[slot]
    elif slot == "extra":
        existing = db.query_one(
            "SELECT extras_json FROM physical_media WHERE manager_job_id=?", (manager_job_id,)
        )
        extras = json.loads(existing["extras_json"] or "[]")
        stem = f"physical-extra-{len(extras)+1:02d}"
    else:
        raise ValueError("Unknown photo slot")
    for old in record_dir.glob(stem + ".*"):
        old.unlink(missing_ok=True)
    target = record_dir / f"{stem}{ext}"
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_bytes(payload)
    os.replace(temp, target)
    relative = target.name
    with db.write() as conn:
        if slot == "extra":
            row = conn.execute(
                "SELECT extras_json FROM physical_media WHERE manager_job_id=?", (manager_job_id,)
            ).fetchone()
            extras = json.loads(row["extras_json"] or "[]")
            extras.append(relative)
            conn.execute(
                "UPDATE physical_media SET extras_json=?,updated_at=? WHERE manager_job_id=?",
                (json.dumps(extras), time.time(), manager_job_id),
            )
        else:
            conn.execute(
                f"UPDATE physical_media SET {slot}_image=?,updated_at=? WHERE manager_job_id=?",
                (relative, time.time(), manager_job_id),
            )
    sync_sidecars(manager_job_id)
    return relative


def delete_image(manager_job_id: str, slot: str, index: Optional[int] = None) -> None:
    _ensure_record(manager_job_id)
    row = db.query_one("SELECT * FROM physical_media WHERE manager_job_id=?", (manager_job_id,))
    filename = None
    with db.write() as conn:
        if slot in IMAGE_NAMES:
            filename = row[f"{slot}_image"]
            conn.execute(
                f"UPDATE physical_media SET {slot}_image=NULL,updated_at=? WHERE manager_job_id=?",
                (time.time(), manager_job_id),
            )
        elif slot == "extra" and index is not None:
            extras = json.loads(row["extras_json"] or "[]")
            if index < 0 or index >= len(extras):
                raise IndexError(index)
            filename = extras.pop(index)
            conn.execute(
                "UPDATE physical_media SET extras_json=?,updated_at=? WHERE manager_job_id=?",
                (json.dumps(extras), time.time(), manager_job_id),
            )
        else:
            raise ValueError("Unknown photo slot")
    if filename:
        (_record_dir(manager_job_id) / filename).unlink(missing_ok=True)
    sync_sidecars(manager_job_id)


def image_path(manager_job_id: str, filename: str) -> Path:
    if Path(filename).name != filename:
        raise ValueError("Invalid image name")
    path = _record_dir(manager_job_id) / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


def _image_url(manager_job_id: str, filename: Optional[str]) -> Optional[str]:
    return f"/archive/{manager_job_id}/images/{filename}" if filename else None


def list_media() -> list[dict]:
    rows = db.query(
        """SELECT j.*,p.front_image,p.rear_image,p.extras_json,p.final_dir
           FROM jobs_history j LEFT JOIN physical_media p USING(manager_job_id)
           ORDER BY COALESCE(j.started_at,j.updated_at) DESC"""
    )
    out = []
    for row in rows:
        item = dict(row)
        extras = json.loads(item.pop("extras_json") or "[]")
        item["front_url"] = _image_url(item["manager_job_id"], item.pop("front_image"))
        item["rear_url"] = _image_url(item["manager_job_id"], item.pop("rear_image"))
        item["extra_urls"] = [_image_url(item["manager_job_id"], name) for name in extras]
        for key in ("verification_json", "raw_json"):
            item.pop(key, None)
        out.append(item)
    return out


def scan_existing() -> dict:
    """Import existing Byte-Me media folders without changing their contents."""
    root = Path(db.get_setting("mover_destination_root", "/media")).resolve()
    folders = db.get_setting_json("mover_destination_folders", {})
    extensions = {".mkv", ".mp4", ".m4v", ".avi", ".flac", ".mp3", ".m4a", ".aac"}
    imported = 0
    seen = 0
    now = time.time()
    for media_type, relative in folders.items():
        base = (root / relative).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            continue
        if not base.is_dir():
            continue
        candidates = {path.parent for path in base.rglob("*") if path.is_file() and path.suffix.lower() in extensions}
        for folder in sorted(candidates):
            seen += 1
            identity = hashlib.sha256(str(folder).encode("utf-8")).hexdigest()[:24]
            manager_job_id = f"existing:{identity}"
            try:
                modified = folder.stat().st_mtime
            except OSError:
                modified = now
            with db.write() as conn:
                created = conn.execute(
                    """INSERT OR IGNORE INTO jobs_history(
                       manager_job_id,node_id,node_job_id,drive,state,title,media_type,
                       started_at,finished_at,progress,last_message,output_dir,
                       verification_json,raw_json,auto_started,cleared,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (manager_job_id, "Byte-Me", identity, "ARCHIVE", "complete", folder.name,
                     media_type, modified, modified, 100, "Existing Byte-Me media", str(folder),
                     json.dumps({"ok": True, "source": "existing_media_scan"}), "{}", 0, 1, now),
                ).rowcount
                conn.execute(
                    "INSERT OR IGNORE INTO physical_media(manager_job_id,final_dir,created_at,updated_at) VALUES (?,?,?,?)",
                    (manager_job_id, str(folder), now, now),
                )
            imported += int(bool(created))
    return {"ok": True, "folders_seen": seen, "imported": imported}


def sync_sidecars(manager_job_id: str, destination: Optional[Path] = None) -> None:
    """Copy archive assets to a reachable final folder using atomic text writes."""
    _ensure_record(manager_job_id)
    row = db.query_one("SELECT * FROM physical_media WHERE manager_job_id=?", (manager_job_id,))
    target = destination or (Path(row["final_dir"]) if row["final_dir"] else None)
    if not target or not target.is_dir():
        return
    text = text_for(manager_job_id)
    temp = target / ".disc-info.txt.tmp"
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, target / "disc-info.txt")
    job = _job(manager_job_id)
    payload = {
        "schema_version": 1,
        "manager_job_id": manager_job_id,
        "title": job.get("title"), "year": job.get("year"),
        "upc": job.get("barcode"), "media_type": job.get("media_type"),
        "creator": job.get("creator"), "narrator": job.get("narrator"),
        "disc": {"season": job.get("season"), "number": job.get("disc")},
        "rip": {"node": job.get("node_id"), "drive": job.get("drive"),
                "started_at": job.get("started_at"), "finished_at": job.get("finished_at"),
                "verification": job.get("verification")},
        "images": {"front": row["front_image"], "rear": row["rear_image"],
                   "extras": json.loads(row["extras_json"] or "[]")},
    }
    json_temp = target / ".disc-info.json.tmp"
    json_temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(json_temp, target / "disc-info.json")
    source_dir = _record_dir(manager_job_id)
    names = [row["front_image"], row["rear_image"], *json.loads(row["extras_json"] or "[]")]
    for name in filter(None, names):
        source = source_dir / name
        if source.is_file():
            (target / name).write_bytes(source.read_bytes())


def set_final_dir(manager_job_id: str, final_dir: Path) -> None:
    _ensure_record(manager_job_id)
    with db.write() as conn:
        conn.execute(
            "UPDATE physical_media SET final_dir=?,updated_at=? WHERE manager_job_id=?",
            (str(final_dir), time.time(), manager_job_id),
        )
    sync_sidecars(manager_job_id, final_dir)
