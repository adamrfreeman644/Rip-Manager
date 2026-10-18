"""Media intake staging and manifest preparation."""
from pathlib import Path
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
                found.setdefault(path.parent.resolve(), media_type)
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
