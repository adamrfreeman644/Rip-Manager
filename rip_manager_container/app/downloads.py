"""Persistent, single-worker yt-dlp queue. URLs never become shell commands or paths."""
from __future__ import annotations

import ipaddress
import re
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import db
from config import DB_PATH

router = APIRouter(prefix="/api/downloads", tags=["downloads"])
_lock = threading.RLock()
_wakeup = threading.Event()
_stop = threading.Event()
_worker = None


def connection():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def initialize():
    with connection() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS download_jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, title TEXT,
          thumbnail TEXT, media_type TEXT NOT NULL, quality TEXT NOT NULL,
          subtitles INTEGER NOT NULL, playlist INTEGER NOT NULL, destination TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued', percent REAL NOT NULL DEFAULT 0,
          speed REAL, eta REAL, error TEXT, output TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        if "custom_name" not in {row[1] for row in con.execute("PRAGMA table_info(download_jobs)")}:
            con.execute("ALTER TABLE download_jobs ADD COLUMN custom_name TEXT")
        con.execute("UPDATE download_jobs SET status='queued', error='Resuming after restart' WHERE status='running'")


def validate_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or len(value) > 2048:
        raise HTTPException(422, "Use a public HTTPS video URL")
    host = parsed.hostname.lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in ("youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com", "soundcloud.com")):
        raise HTTPException(422, "Supported sites: YouTube, Vimeo and SoundCloud")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HTTPException(422, "IP addresses are not supported")
    return value


class AddJob(BaseModel):
    url: str = Field(max_length=2048)
    media_type: str = Field(pattern="^(video|audio)$")
    quality: str = Field(pattern="^(best|1080|720|480)$")
    subtitles: bool = False
    playlist: bool = False
    destination: str = Field(pattern="^(downloads|movie|tv|music|audiobook)$")
    custom_name: str = Field(default="", max_length=180)
    selected_indices: list[int] | None = Field(default=None, max_length=250)


def clean_name(value):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|%]', "", value).strip(" .")
    if name in ("", ".", "..") or not name.strip():
        raise ValueError("Enter a valid filename")
    return name[:180]

def output_template(target, playlist, custom_name):
    if playlist:
        return str(target / "%(playlist_index)02d - %(title).180B.%(ext)s")
    return str(target / f"{clean_name(custom_name)}.%(ext)s") if custom_name else str(target / "%(title).180B [%(id)s].%(ext)s")

def destination_path(preset):
    root = Path(db.get_setting("mover_destination_root", "/media")).resolve()
    folders = db.get_setting_json("mover_destination_folders", {})
    relative = "Downloads" if preset == "downloads" else folders.get(preset, {"movie":"Movies","tv":"TV","music":"Music","audiobook":"Audiobooks"}[preset])
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Invalid media destination")
    return candidate


def playlist_entries(info):
    entries = info.get("entries") or []
    if len(entries) > 250:
        raise HTTPException(422, "Playlist is too large; maximum 250 videos")
    result = []
    for index, entry in enumerate(entries, 1):
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url") or ""
        if not str(url).startswith("https://") and entry.get("ie_key", "").lower().startswith("youtube") and entry.get("id"):
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        try:
            validate_url(url)
        except HTTPException:
            continue
        result.append({"index": index, "url": url, "title": entry.get("title") or f"Video {index}", "duration": entry.get("duration")})
    return result


def extract_playlist(url):
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": False,
                          "extract_flat": "in_playlist", "socket_timeout": 15, "retries": 1}) as ydl:
        return ydl.extract_info(url, download=False)


@router.post("/preview")
def preview(request: AddJob):
    validate_url(request.url)
    try:
        if request.playlist:
            info = extract_playlist(request.url)
            if info.get("_type") != "playlist":
                raise HTTPException(422, "This link is not a playlist")
            entries = playlist_entries(info)
            if not entries:
                raise HTTPException(422, "No downloadable videos found in playlist")
            return {"title": info.get("title"), "playlist": True, "entries": entries}
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True,
                              "extract_flat": True, "socket_timeout": 10, "retries": 1}) as ydl:
            info = ydl.extract_info(request.url, download=False)
        return {"title": info.get("title"), "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"), "playlist": False}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Cannot preview URL: {exc}") from exc


@router.post("")
def add(request: AddJob):
    validate_url(request.url)
    try:
        path = destination_path(request.destination)
        name = clean_name(request.custom_name) if request.custom_name and not request.playlist else ""
        if not path.parent.exists():
            raise ValueError("Media root is not mounted")
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if request.playlist:
        try:
            info = extract_playlist(request.url)
            if info.get("_type") != "playlist":
                raise HTTPException(422, "This link is not a playlist")
            entries = playlist_entries(info)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, f"Cannot load playlist: {exc}") from exc
        selected = set(request.selected_indices) if request.selected_indices is not None else {item["index"] for item in entries}
        if not selected or not selected.issubset({item["index"] for item in entries}):
            raise HTTPException(422, "Select at least one available playlist video")
        jobs = []
        with _lock, connection() as con:
            for item in entries:
                if item["index"] not in selected:
                    continue
                filename = clean_name(f"{item['index']:02d} - {item['title']}")
                cur = con.execute("INSERT INTO download_jobs(url,media_type,quality,subtitles,playlist,destination,custom_name,title) VALUES(?,?,?,?,?,?,?,?)",
                    (item["url"], request.media_type, request.quality, int(request.subtitles), 0, request.destination, filename, filename))
                jobs.append(cur.lastrowid)
        _wakeup.set()
        return {"ids": jobs, "count": len(jobs), "status": "queued"}
    with _lock, connection() as con:
        cur = con.execute("INSERT INTO download_jobs(url,media_type,quality,subtitles,playlist,destination,custom_name,title) VALUES(?,?,?,?,?,?,?,?)",
                          (request.url, request.media_type, request.quality, int(request.subtitles), 0, request.destination, name, name or None))
        job_id = cur.lastrowid
    _wakeup.set()
    return {"id": job_id, "count": 1, "status": "queued"}


@router.get("")
def list_jobs():
    with connection() as con:
        return [dict(row) for row in con.execute("SELECT * FROM download_jobs ORDER BY id DESC LIMIT 500")]


@router.delete("/{job_id}")
def remove(job_id: int):
    with _lock, connection() as con:
        row = con.execute("SELECT status FROM download_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Unknown download")
        if row["status"] == "running":
            raise HTTPException(409, "Wait for the active download to finish")
        con.execute("DELETE FROM download_jobs WHERE id=?", (job_id,))
    return {"ok": True}


@router.post("/{job_id}/retry")
def retry(job_id: int):
    with _lock, connection() as con:
        cur = con.execute("UPDATE download_jobs SET status='queued',error=NULL,percent=0 WHERE id=? AND status='failed'", (job_id,))
        if not cur.rowcount:
            raise HTTPException(409, "Only failed downloads can be retried")
    _wakeup.set()
    return {"ok": True}


def update(job_id, **fields):
    with _lock, connection() as con:
        fields["updated_at"] = None
        assignments = ",".join(f"{key}={'CURRENT_TIMESTAMP' if key == 'updated_at' else '?'}" for key in fields)
        con.execute(f"UPDATE download_jobs SET {assignments} WHERE id=?", [v for k, v in fields.items() if k != "updated_at"] + [job_id])


def run_job(row):
    job_id = row["id"]
    target = destination_path(row["destination"])
    target.mkdir(parents=True, exist_ok=True)
    # yt-dlp sanitizes metadata filenames; user names are confined to one basename.
    fmt = "bestaudio/best" if row["media_type"] == "audio" else ("bv*+ba/b" if row["quality"] == "best" else f"bv*[height<={row['quality']}]+ba/b[height<={row['quality']}]")
    displayed_title = row.get("title")
    def progress(data):
        nonlocal displayed_title
        info = data.get("info_dict") or {}
        discovered = row.get("custom_name") or (info.get("playlist_title") or info.get("title"))
        if discovered and discovered != displayed_title:
            displayed_title = discovered
            update(job_id, title=str(discovered)[:240])
        if data.get("status") == "downloading":
            update(job_id, percent=round(100 * data.get("downloaded_bytes", 0) / max(data.get("total_bytes") or data.get("total_bytes_estimate") or 1, 1), 1), speed=data.get("speed"), eta=data.get("eta"))
    template = output_template(target, bool(row["playlist"]), row.get("custom_name") or "")
    if row.get("custom_name") and not row["playlist"] and list(target.glob(f"{clean_name(row['custom_name'])}.*")):
        raise ValueError("That filename already exists in the destination")
    options = {"format": fmt, "outtmpl": template,
               "noplaylist": not bool(row["playlist"]), "paths": {"home": str(target), "temp": str(target / ".incomplete")},
               "progress_hooks": [progress], "socket_timeout": 20, "retries": 3,
               "continuedl": True, "restrictfilenames": True, "quiet": True, "no_warnings": True,
               "writesubtitles": bool(row["subtitles"]), "subtitleslangs": ["en.*"],
               "merge_output_format": "mkv", "postprocessors": ([{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}] if row["media_type"] == "audio" else [])}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(row["url"], download=True)
    info = info or {}
    update(job_id, title=row.get("custom_name") or info.get("title") or displayed_title, thumbnail=info.get("thumbnail"), percent=100, speed=None, eta=None, output=str(target), status="completed")


def loop():
    while not _stop.is_set():
        with _lock, connection() as con:
            row = con.execute("SELECT * FROM download_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                con.execute("UPDATE download_jobs SET status='running' WHERE id=?", (row["id"],))
        if row:
            try:
                run_job(dict(row))
            except Exception as exc:
                update(row["id"], status="failed", error=str(exc)[:500], speed=None, eta=None)
        else:
            _wakeup.wait(2)
            _wakeup.clear()


def start():
    global _worker
    initialize()
    _stop.clear()
    _worker = threading.Thread(target=loop, name="yt-dlp-worker", daemon=True)
    _worker.start()


def stop():
    _stop.set()
    _wakeup.set()
    if _worker:
        _worker.join(timeout=2)
