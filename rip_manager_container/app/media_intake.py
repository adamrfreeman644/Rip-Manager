"""Media intake staging and manifest preparation."""
from pathlib import Path
import hashlib
import json
import time
import archive
import db

MEDIA_DIRS = {"movie": "Movies", "tv": "TV", "music": "Music", "audiobook": "Audiobooks"}
MEDIA_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".flac", ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"}

def root():
    media = Path(db.get_setting("mover_destination_root", "/media")).resolve()
    return (media / "0 ~ Intake").resolve()

def ensure():
    base = root()
    for name in MEDIA_DIRS.values():
        (base / name).mkdir(parents=True, exist_ok=True)
    return base

def folder_for(media_type, title):
    if media_type not in MEDIA_DIRS:
        raise ValueError("Unsupported media type")
    safe = "".join(ch for ch in title.strip() if ch not in '/\\:*?"<>|').strip(" .")[:180]
    if not safe:
        raise ValueError("A title is required")
    return ensure() / MEDIA_DIRS[media_type] / safe

def scan():
    base = ensure()
    found = {}
    for media_type, name in MEDIA_DIRS.items():
        section = base / name
        for path in section.rglob("*"):
            if path.is_file() and path.suffix.lower() in MEDIA_EXT:
                relative = path.relative_to(section)
                if relative.parts:
                    # One immediate child is one intake unit; preserve TV seasons,
                    # album subfolders, subtitles, artwork and other nested files.
                    found.setdefault((section / relative.parts[0]).resolve(), media_type)
    items = []
    for folder, media_type in sorted(found.items(), key=lambda item: str(item[0]).casefold()):
        items.append({"folder": str(folder), "name": folder.name, "media_type": media_type,
                      "manifest": (folder / "disc-info.json").is_file()})
    return {"root": str(base), "count": len(items), "items": items}

def prepare_all():
    created = existing = failed = 0
    errors = []
    data = scan()
    for item in data["items"]:
        folder = Path(item["folder"])
        if item["manifest"]:
            existing += 1
            continue
        try:
            archive.write_recovery_manifest(folder, title=folder.name,
                                            media_type=item["media_type"], node_id="media_intake")
            created += 1
        except (OSError, ValueError) as exc:
            failed += 1
            errors.append({"folder": str(folder), "error": str(exc)})
    return {"created": created, "existing": existing, "failed": failed, "errors": errors, **scan()}

def queue_all():
    """Adopt prepared intake folders into the normal verified Mover queue."""
    import mover
    prepared = prepare_all()
    queued = existing_jobs = 0
    now = time.time()
    for item in prepared["items"]:
        folder = Path(item["folder"]).resolve()
        identity = hashlib.sha256(str(folder).encode("utf-8")).hexdigest()[:24]
        job_id = "intake:" + identity
        with db.write() as conn:
            inserted = conn.execute(
                """INSERT OR IGNORE INTO jobs_history(
                   manager_job_id,node_id,node_job_id,drive,state,title,media_type,
                   started_at,finished_at,progress,last_message,output_dir,
                   verification_json,raw_json,auto_started,cleared,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, "Media-Intake", identity, "INTAKE", "complete", folder.name,
                 item["media_type"], now, now, 100, "Media intake ready", str(folder),
                 json.dumps({"ok": True, "source": "media_intake"}), "{}", 0, 0, now),
            ).rowcount
            conn.execute(
                "INSERT OR IGNORE INTO physical_media(manager_job_id,final_dir,created_at,updated_at) VALUES (?,?,?,?)",
                (job_id, str(folder), now, now),
            )
        if inserted:
            queued += int(mover.enqueue(job_id))
        else:
            existing_jobs += 1
    return {"queued": queued, "already_adopted": existing_jobs, **prepared}
