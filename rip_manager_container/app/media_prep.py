"""Post-rip media preparation used by the mover."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".webm"}


def _duration(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, check=False,
        )
        return float((result.stdout or "0").strip() or 0) if result.returncode == 0 else 0.0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def _videos(root):
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS),
        key=lambda path: str(path).lower(),
    )


def _safe(text):
    return re.sub(r'[\\/:*?"<>|]+', " ", text).strip() or "Untitled"


def _play_all(files, durations):
    positive = [path for path in files if durations.get(path, 0) > 0]
    if len(positive) < 3:
        return None
    longest = max(positive, key=lambda path: durations[path])
    others = [path for path in positive if path != longest]
    total = sum(durations[path] for path in others)
    return longest if total > 0 and abs(durations[longest] - total) <= max(20.0, total * .02) else None


def prepare_completed_rip(source, job):
    source = Path(source)
    marker = source / "disc-info.txt"
    if job.get("state") != "complete":
        raise ValueError("Media prep requires a completed rip")
    if not marker.is_file():
        raise ValueError("Media prep marker disc-info.txt is missing")
    files = _videos(source)
    if not files:
        return {"ok": True, "prepared": 0, "extras": 0, "play_all_removed": 0}
    media_type = (job.get("media_type") or "").lower()
    if media_type not in {"tv", "movie"}:
        return {"ok": True, "prepared": 0, "extras": 0, "play_all_removed": 0}

    durations = {path: _duration(path) for path in files}
    title = _safe(job.get("title") or source.name)
    disc, season = int(job.get("disc") or 1), int(job.get("season") or 0)
    changes = []
    if media_type == "tv":
        play_all = _play_all(files, durations)
        if play_all:
            changes.append({"path": play_all.relative_to(source).as_posix(), "action": "deleted_play_all"})
            play_all.unlink()
            files.remove(play_all)
            durations.pop(play_all, None)

    extras = set()
    longest = max((durations.get(path, 0) for path in files), default=0)
    if longest > 0:
        if media_type == "movie":
            main = max(files, key=lambda path: durations.get(path, 0))
            extras = {path for path in files if path != main and durations.get(path, 0) < longest * .75}
        else:
            extras = {path for path in files if durations.get(path, 0) > 0 and durations[path] < longest * .70}

    extras_dir, prepared = source / "extras", 0
    for number, path in enumerate(list(files), 1):
        suffix = path.suffix.lower()
        stem = (f"{title} S{season:02d} D{disc:02d}F{number:02d}" if media_type == "tv" else
                (title if path not in extras and number == 1 else f"{title} D{disc:02d}F{number:02d}"))
        target_dir = extras_dir if path in extras else source
        target_dir.mkdir(parents=True, exist_ok=True)
        target, duplicate = target_dir / f"{stem}{suffix}", 2
        while target.exists() and target.resolve() != path.resolve():
            target = target_dir / f"{stem}-{duplicate}{suffix}"
            duplicate += 1
        if path.resolve() != target.resolve():
            changes.append({"old_paths": [path.relative_to(source).as_posix()], "action": "moved",
                            "current_path": target.relative_to(source).as_posix()})
            shutil.move(str(path), str(target))
        prepared += 1

    for folder in sorted((path for path in source.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass
    report = {"ok": True, "prepared": prepared, "extras": len(extras),
              "play_all_removed": sum(item["action"] == "deleted_play_all" for item in changes),
              "media_type": media_type, "season": season if media_type == "tv" else None, "disc": disc}
    (source / "rip-manager-prep.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if changes:
        import archive
        archive.record_file_changes(source, changes + [{"path": "rip-manager-prep.json", "action": "written"}])
    return report
