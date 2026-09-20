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
import subprocess
from datetime import datetime, timezone
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


def existing_folder(item: dict) -> Optional[Path]:
    """Return the real folder backing a row, or None for stale history."""
    if item.get("final_dir"):
        final_dir = Path(item["final_dir"])
        if final_dir.is_dir():
            return final_dir

    output = Path(item.get("output_dir") or "")
    if item.get("node_id") == "Byte-Me" and output.is_dir():
        return output
    if not output.is_absolute():
        return None

    node_id = item.get("node_id")
    mounts = db.get_setting_json("mover_node_mounts", {})
    source_roots = db.get_setting_json("mover_node_source_roots", {})
    configured_mount = mounts.get(node_id)
    if not configured_mount:
        return None
    mount = Path(configured_mount).resolve()
    try:
        relative = output.relative_to(Path(source_roots.get(node_id, "/mnt/ripping")))
        candidate = (mount / relative).resolve()
        candidate.relative_to(mount)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_dir() else None


def list_media() -> list[dict]:
    rows = db.query(
        """SELECT j.*,p.front_image,p.rear_image,p.extras_json,p.final_dir
           FROM jobs_history j LEFT JOIN physical_media p USING(manager_job_id)
           ORDER BY COALESCE(j.started_at,j.updated_at) DESC"""
    )
    out = []
    for row in rows:
        item = dict(row)
        folder = existing_folder(item)
        extras = json.loads(item.pop("extras_json") or "[]")
        item["storage_available"] = folder is not None
        item["existing_dir"] = str(folder) if folder else None
        item["expected_dir"] = expected_folder(item)
        item["front_url"] = _image_url(item["manager_job_id"], item.pop("front_image"))
        item["rear_url"] = _image_url(item["manager_job_id"], item.pop("rear_image"))
        item["extra_urls"] = [_image_url(item["manager_job_id"], name) for name in extras]
        for key in ("verification_json", "raw_json"):
            item.pop(key, None)
        out.append(item)
    return out


def expected_folder(item: dict) -> Optional[str]:
    """Describe where a missing record should be reachable from Manager."""
    if item.get("final_dir"):
        return str(item["final_dir"])
    output = Path(item.get("output_dir") or "")
    if not output.is_absolute():
        return str(output) if str(output) else None
    if item.get("node_id") == "Byte-Me":
        return str(output)
    node_id = item.get("node_id")
    mounts = db.get_setting_json("mover_node_mounts", {})
    roots = db.get_setting_json("mover_node_source_roots", {})
    mount = mounts.get(node_id)
    if not mount:
        return f"Configure a Manager mount for {node_id}"
    try:
        return str(Path(mount) / output.relative_to(Path(roots.get(node_id, "/mnt/ripping"))))
    except ValueError:
        return f"{output} is outside the configured Node output root"


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def file_metadata(path: Path, relative_to: Optional[Path] = None) -> dict:
    """Return reliable on-disk and ffprobe metadata, tolerating unsupported files."""
    stat = path.stat()
    item = {
        "path": path.relative_to(relative_to).as_posix() if relative_to else path.name,
        "size_bytes": stat.st_size,
        "modified_at": _iso_timestamp(stat.st_mtime),
        "extension": path.suffix.lower() or None,
    }
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
        )
        decoded = json.loads(probe.stdout) if probe.returncode == 0 else {}
        fmt = decoded.get("format") or {}
        if fmt:
            item["container"] = fmt.get("format_name")
            item["duration_seconds"] = float(fmt["duration"]) if fmt.get("duration") else None
            item["bit_rate"] = int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None
            item["metadata"] = fmt.get("tags") or {}
        streams = []
        for stream in decoded.get("streams") or []:
            streams.append({key: stream.get(key) for key in (
                "index", "codec_type", "codec_name", "codec_long_name", "profile",
                "bit_rate", "width", "height", "pix_fmt", "r_frame_rate",
                "sample_rate", "channels", "channel_layout", "language",
            ) if stream.get(key) is not None} | ({"metadata": stream["tags"]} if stream.get("tags") else {}))
        if streams:
            item["streams"] = streams
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return item


def _existing_files(folder: Path) -> list[dict]:
    """Inventory a legacy folder without changing its media."""
    files = []
    try:
        candidates = sorted(path for path in folder.rglob("*") if path.is_file())
    except OSError:
        candidates = []
    for path in candidates:
        if path.name in {"disc-info.json", "disc-info.txt"}:
            continue
        try:
            files.append(file_metadata(path, folder))
        except OSError:
            continue
    return files




def refresh_file_inventory(folder: Path, payload: Optional[dict] = None) -> dict:
    """Refresh the live inventory while retaining per-file process history by path."""
    if payload is None:
        target = Path(folder) / "disc-info.json"
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
    previous = {item.get("path"): item for item in payload.get("files") or []}
    files = _existing_files(Path(folder))
    for item in files:
        history = previous.get(item["path"], {}).get("process_history")
        if isinstance(history, list):
            item["process_history"] = history
    payload["files"] = files
    payload["file_structure"] = folder_structure(Path(folder))
    return payload

def folder_structure(folder: Path) -> dict:
    """Describe the complete reachable folder tree; file metadata lives in files."""
    try:
        directories = sorted(
            path.relative_to(folder).as_posix()
            for path in folder.rglob("*") if path.is_dir()
        )
    except OSError:
        directories = []
    return {"root": folder.name, "directories": directories}

def _merge_manifest(existing: dict, generated: dict, refresh: bool = False) -> dict:
    """Keep curated facts while refreshing the scanner's observed file facts."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in generated.items():
        observed = refresh or key in {"schema_version", "legacy_import", "folder", "files", "file_structure"}
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_manifest(merged[key], value, refresh=observed)
        elif observed or key not in merged or merged[key] is None:
            merged[key] = value
    return merged



def record_file_changes(folder: Path, changes: list[dict]) -> bool:
    """Append actual program file changes; reads and access are deliberately omitted."""
    if not changes:
        return False
    target = Path(folder) / "disc-info.json"
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
    except (OSError, ValueError, TypeError):
        return False
    log = payload.setdefault("change_log", [])
    if not isinstance(log, list):
        log = payload["change_log"] = []
    log.append({"at": _iso_timestamp(time.time()), "changes": changes})
    temp = target.with_name(".disc-info.json.tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, target)
    return True


def record_process_event(folder: Path, event: str, status: str = "complete",
                         details: Optional[dict] = None) -> bool:
    """Append a folder lifecycle milestone; ordinary reads never call this."""
    target = Path(folder) / "disc-info.json"
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
    except (OSError, ValueError, TypeError):
        return False
    entry = {"event": event, "status": status, "at": _iso_timestamp(time.time())}
    if details:
        entry["details"] = details
    payload.setdefault("process_history", []).append(entry)
    temp = target.with_name(".disc-info.json.tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, target)
    return True


def record_file_process_events(folder: Path, paths: list[str], event: str,
                               status: str = "complete", details: Optional[dict] = None) -> bool:
    """Record a lifecycle outcome only against the named files that experienced it."""
    target = Path(folder) / "disc-info.json"
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
    except (OSError, ValueError, TypeError):
        return False
    wanted = set(paths)
    changed = False
    for item in payload.get("files") or []:
        if item.get("path") not in wanted:
            continue
        entry = {"event": event, "status": status, "at": _iso_timestamp(time.time())}
        if details:
            entry["details"] = details
        item.setdefault("process_history", []).append(entry)
        changed = True
    if not changed:
        return False
    temp = target.with_name(".disc-info.json.tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, target)
    return True

def write_existing_manifest(manager_job_id: str, folder: Path) -> str:
    """Create or refresh the JSON-only manifest for an imported media folder."""
    job = _job(manager_job_id)
    try:
        folder_stat = folder.stat()
    except OSError:
        raise
    target = folder / "disc-info.json"
    previous = {}
    if target.is_file():
        try:
            decoded = json.loads(target.read_text(encoding="utf-8"))
            previous = decoded if isinstance(decoded, dict) else {}
        except (OSError, ValueError, TypeError):
            # Never discard a non-JSON or damaged prior file: leave it untouched.
            return "skipped"
    generated = {
        "schema_version": 1,
        "manager_job_id": manager_job_id,
        "title": job.get("title") or folder.name,
        "year": job.get("year"),
        "upc": job.get("barcode"),
        "media_type": job.get("media_type"),
        "creator": job.get("creator"),
        "narrator": job.get("narrator"),
        "disc": {"season": job.get("season"), "number": job.get("disc")},
        "legacy_import": {
            "imported": True,
            "source": "existing_media_scan",
            "historical_rip_details_available": False,
            "note": "Fields unavailable from the existing folder are null; no rip history was invented.",
        },
        "folder": {
            "name": folder.name,
            "path": str(folder),
            "modified_at": _iso_timestamp(folder_stat.st_mtime),
        },
        "files": _existing_files(folder),
        "file_structure": folder_structure(folder),
        "rip": {
            "node": None,
            "drive": None,
            "started_at": None,
            "finished_at": None,
            "verification": None,
        },
    }
    payload = _merge_manifest(previous, generated)
    payload = refresh_file_inventory(folder, payload)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if target.is_file():
        try:
            if target.read_text(encoding="utf-8") == rendered:
                return "unchanged"
        except OSError:
            pass
    temp = folder / ".disc-info.json.tmp"
    temp.write_text(rendered, encoding="utf-8")
    os.replace(temp, target)
    return "updated" if previous else "created"


def scan_existing() -> dict:
    """Index every configured existing-media folder and maintain JSON manifests."""
    root = Path(db.get_setting("mover_destination_root", "/media")).resolve()
    folders = db.get_setting_json("mover_destination_folders", {})
    extensions = {".mkv", ".mp4", ".m4v", ".avi", ".flac", ".mp3", ".m4a", ".aac"}
    imported = 0
    seen_folders: dict[Path, str] = {}
    now = time.time()
    for media_type, relative in folders.items():
        base = (root / relative).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            continue
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in extensions:
                seen_folders.setdefault(path.parent, media_type)

    records = {}
    for folder, media_type in sorted(seen_folders.items()):
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
        records[folder] = manager_job_id

    # Include already imported/stored rows, so reruns repair the earlier 745 records too.
    for row in db.query(
        """SELECT j.manager_job_id,j.media_type,p.final_dir
           FROM jobs_history j JOIN physical_media p USING(manager_job_id)
           WHERE p.final_dir IS NOT NULL"""
    ):
        folder = Path(row["final_dir"])
        try:
            resolved = folder.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_dir():
            records.setdefault(resolved, row["manager_job_id"])

    created = updated = unchanged = skipped = 0
    for folder, manager_job_id in sorted(records.items()):
        result = write_existing_manifest(manager_job_id, folder)
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        elif result == "unchanged":
            unchanged += 1
        else:
            skipped += 1
    return {
        "ok": True,
        "folders_seen": len(seen_folders),
        "records_processed": len(records),
        "imported": imported,
        "manifests_created": created,
        "manifests_updated": updated,
        "manifests_unchanged": unchanged,
        "manifests_skipped": skipped,
    }

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
    manifest = target / "disc-info.json"
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    except (OSError, ValueError, TypeError):
        existing = {}
    payload = _merge_manifest(existing, payload)
    # Every program-managed folder carries a complete current inventory and tree.
    # disc-info.json itself is intentionally excluded from its own metadata to avoid
    # a self-referential size/timestamp update loop.
    payload = refresh_file_inventory(target, payload)
    json_temp = target / ".disc-info.json.tmp"
    json_temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(json_temp, manifest)
    source_dir = _record_dir(manager_job_id)
    names = [row["front_image"], row["rear_image"], *json.loads(row["extras_json"] or "[]")]
    copied = []
    for name in filter(None, names):
        source = source_dir / name
        if source.is_file():
            (target / name).write_bytes(source.read_bytes())
            copied.append({"path": name, "action": "written"})
    record_file_changes(target, [
        {"path": "disc-info.txt", "action": "written"},
        {"path": "disc-info.json", "action": "updated"},
        *copied,
    ])


def set_final_dir(manager_job_id: str, final_dir: Path) -> None:
    _ensure_record(manager_job_id)
    with db.write() as conn:
        conn.execute(
            "UPDATE physical_media SET final_dir=?,updated_at=? WHERE manager_job_id=?",
            (str(final_dir), time.time(), manager_job_id),
        )
    sync_sidecars(manager_job_id, final_dir)
