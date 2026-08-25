#!/usr/bin/env python3
"""
Rip Node FastAPI backend
========================

Purpose:
- Expose a small HTTP API for a headless Ubuntu ripping node.
- Report optical-drive, tray and system status.
- Eject/close trays.
- Select MakeMKV or an audio-CD engine from the requested media type.
- Keep HandBrake separate from the node workflow.

Install:
    sudo apt update
    sudo apt install python3-pip eject makemkv-bin ffmpeg abcde cdparanoia cd-discid flac lame
    python3 -m pip install fastapi uvicorn psutil

Run:
    python3 rip_node_api.py

Then open:
    http://NODE-IP:8000/docs

IMPORTANT:
Edit DRIVE_MAP below to match your real drive devices/symlinks.
For production, use persistent udev symlinks rather than /dev/sr0 etc.


What changed in 0.2.8
---------------------
- Adds authenticated storage settings endpoints so Rip Manager can inspect and
  change the output directory without SSH or rewriting the systemd service.
- Storage changes are blocked during active rips and require an existing,
  writable absolute directory.

What changed in 0.2.7
---------------------
- Refreshes hot-plugged optical hardware on request so newly connected drives
  appear immediately in Rip Manager's assignment selectors.

What changed in 0.2.6
---------------------
- A finished failed/cancelled job can be cleared remotely without deleting any
  ripped output files.

What changed in 0.2.4
----------------------
- Adds persistent drive mapping managed over the API.
- GET /drive-mapping lists configured slots and currently detected optical drives.
- PUT /drive-mapping/{name} adds, remaps or swaps a slot without SSH.
- DELETE /drive-mapping/{name} removes a slot when it is not ripping.
- Mappings prefer udev ID_PATH so /dev/srX renumbering does not break the slot.

What changed in 0.2.3
----------------------
- Adds live rip-health diagnostics for Rip Manager: process state, elapsed time,
  time since MakeMKV/abcde activity, time since progress changed, output growth,
  warnings, and Active / Slow-retrying / Possibly stalled / Stopped states.
- MakeMKV A/V sync and similar recoverable messages are surfaced as warnings
  rather than being treated as a failed rip.
- When a disc is physically removed/ejected, the drive returns to a clean
  new-disc state. Current disc/job/progress/warning details are cleared from the
  drive payload; only the previous Title, Year, Season and Disc are retained.
- Adds a per-drive /health endpoint for the web UI.
- Preserves the 0.2.1 per-drive DVD/CD read-speed reporting.

What changed in 0.2.1
----------------------
- System statistics now report live per-drive read throughput, calculated from
  the files being written by each active rip. Video is shown as DVD-equivalent
  speed and audio as CD-equivalent speed.

What changed in 0.2.0
----------------------
- Four routed libraries under RIP_NODE_OUTPUT: Movies, TV, Music and
  AudioBooks. Movie/TV jobs use MakeMKV; Music/Audiobook jobs use abcde.
- Music is encoded to tagged FLAC. Audiobooks are encoded to compatible MP3
  tracks and placed in Audiobookshelf-friendly Author/Book/Disc folders.
- Verification now selects video or audio streams to match the job.

What changed in 0.1.16
----------------------
- Real tray detection. The CDROM_DRIVE_STATUS ioctl distinguishes an open
  tray from an empty closed drive, so the manager can finally show OPEN and
  offer the correct Open/Close tray control.
- Far cheaper polling. The ioctl costs microseconds, so blkid now runs only
  when a disc is actually loaded, and udev properties are cached because a
  drive's model and serial never change while it is plugged in.
- Rip requests may carry barcode, media type and a per-job auto-eject
  preference, so the manager's own settings finally reach the node.
- Finished jobs are pruned, and system statistics are cached briefly, so a
  two-second manager poll no longer walks the whole process table each time.
"""

from __future__ import annotations

import fcntl
import json
import os
import platform
import re
import shlex
import signal
import shutil
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

import psutil
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

NODE_NAME = os.getenv("RIP_NODE_NAME", "rip-node-1")
API_TOKEN = os.getenv("RIP_NODE_TOKEN", "")  # blank = no auth
HOST = os.getenv("RIP_NODE_HOST", "0.0.0.0")
PORT = int(os.getenv("RIP_NODE_PORT", "8000"))

# Drive mapping is persistent and can be managed from Rip Manager.
# Existing installations start with the legacy DVD1/DVD2/DVD3 symlinks, then
# save changes to DRIVE_MAP_FILE. Each slot stores ID_PATH where available so
# the mapping survives /dev/srX renumbering and physical drive replacement.
LEGACY_DRIVE_MAP_FILE = Path("/opt/rip-node/drive-map.json")
DRIVE_MAP_FILE = Path(os.getenv(
    "RIP_NODE_DRIVE_MAP",
    str(Path.home() / ".config" / "rip-node" / "drive-map.json"),
))
DEFAULT_DRIVE_MAP = {
    "DVD1": {"device": "/dev/ripper/DVD1", "id_path": None},
    "DVD2": {"device": "/dev/ripper/DVD2", "id_path": None},
    "DVD3": {"device": "/dev/ripper/DVD3", "id_path": None},
}
drive_map_lock = threading.RLock()

def load_drive_map() -> dict:
    try:
        source = DRIVE_MAP_FILE if DRIVE_MAP_FILE.exists() else LEGACY_DRIVE_MAP_FILE
        if source.exists():
            payload = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                cleaned = {}
                for name, entry in payload.items():
                    key = str(name).upper().strip()
                    if not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,19}", key):
                        continue
                    if isinstance(entry, str):
                        entry = {"device": entry, "id_path": None}
                    if isinstance(entry, dict) and entry.get("device"):
                        cleaned[key] = {
                            "device": str(entry["device"]),
                            "id_path": entry.get("id_path"),
                            "model": entry.get("model"),
                            "serial": entry.get("serial"),
                        }
                if cleaned:
                    return cleaned
    except (OSError, ValueError, TypeError):
        pass
    return {k: dict(v) for k, v in DEFAULT_DRIVE_MAP.items()}

DRIVE_MAP = load_drive_map()

def save_drive_map() -> None:
    DRIVE_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DRIVE_MAP_FILE.with_suffix(".json.tmp")
    with drive_map_lock:
        tmp.write_text(json.dumps(DRIVE_MAP, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(DRIVE_MAP_FILE)

NODE_SETTINGS_FILE = Path(os.getenv(
    "RIP_NODE_SETTINGS",
    str(Path.home() / ".config" / "rip-node" / "node-settings.json"),
))
node_settings_lock = threading.RLock()


def load_node_settings() -> dict:
    try:
        payload = json.loads(NODE_SETTINGS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_node_settings(payload: dict) -> None:
    NODE_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = NODE_SETTINGS_FILE.with_suffix(".json.tmp")
    with node_settings_lock:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(NODE_SETTINGS_FILE)


RIP_ROOT = Path(load_node_settings().get("output_path") or os.getenv("RIP_NODE_OUTPUT", "/mnt/ripping"))
MIN_TITLE_SECONDS = int(os.getenv("RIP_NODE_MIN_TITLE", "120"))
AUTO_EJECT_AFTER_RIP = os.getenv("RIP_NODE_AUTO_EJECT", "1") == "1"

# MakeMKV track-selection preferences.
# Keep all titles >= MIN_TITLE_SECONDS, but only English or undefined-language
# audio/subtitle tracks. "nolang" protects useful tracks that discs failed to tag.
MAKEMKV_PREFERRED_LANGUAGE = os.getenv("RIP_NODE_LANGUAGE", "eng")
MAKEMKV_DEFAULT_SELECTION = os.getenv(
    "RIP_NODE_SELECTION",
    "-sel:all,+sel:(eng|nolang),-sel:mvcvideo,=100:all,-10:eng",
)

# Node sound files. MP3 uses mpg123; WAV uses aplay.
SOUND_DIR = Path(os.getenv("RIP_NODE_SOUND_DIR", "/opt/rip-node/sounds"))
COMPLETE_SOUND = os.getenv("RIP_NODE_COMPLETE_SOUND", str(SOUND_DIR / "Complete.mp3"))
NEEDS_ATTENTION_SOUND = os.getenv("RIP_NODE_NEEDS_ATTENTION_SOUND", str(SOUND_DIR / "NeedsAttention.mp3"))
FAIL_SOUND = os.getenv("RIP_NODE_FAIL_SOUND", str(SOUND_DIR / "Failed.mp3"))
VERIFY_FAIL_SOUND = os.getenv("RIP_NODE_VERIFY_FAIL_SOUND", str(SOUND_DIR / "VerifyFailed.mp3"))

# Verification: every produced MKV must exist, be non-trivially sized, and
# ffprobe must be able to read at least one video stream from it.
MIN_VERIFIED_MKV_BYTES = int(os.getenv("RIP_NODE_MIN_MKV_BYTES", str(5 * 1024 * 1024)))
MIN_VERIFIED_AUDIO_BYTES = int(os.getenv("RIP_NODE_MIN_AUDIO_BYTES", str(100 * 1024)))
MEDIA_PROBE_TIMEOUT = float(os.getenv("RIP_NODE_MEDIA_PROBE_TIMEOUT", "10"))
MEDIA_CACHE_GRACE = float(os.getenv("RIP_NODE_MEDIA_CACHE_GRACE", "60"))

# Static hardware facts do not change while a drive is plugged in, so the
# udevadm subprocess does not need to run on every manager poll.
UDEV_CACHE_SECONDS = float(os.getenv("RIP_NODE_UDEV_CACHE", "600"))

# The manager polls statistics every couple of seconds during a rip. Walking
# the whole process table that often is pure waste, so results are reused.
STATS_CACHE_SECONDS = float(os.getenv("RIP_NODE_STATS_CACHE", "2"))

# Finished jobs stay in memory so the GUI can show recent history, but the
# node runs for months at a time and the list must not grow without limit.
MAX_FINISHED_JOBS = int(os.getenv("RIP_NODE_MAX_FINISHED_JOBS", "200"))

# Rip-health thresholds. Optical media can legitimately pause while retrying a
# damaged/dirty area, so "possibly stalled" is deliberately conservative.
RIP_HEALTH_SLOW_SECONDS = float(os.getenv("RIP_NODE_HEALTH_SLOW_SECONDS", "120"))
RIP_HEALTH_STALL_SECONDS = float(os.getenv("RIP_NODE_HEALTH_STALL_SECONDS", "300"))

ACTIVE_STATES = {"starting", "ripping", "verifying", "cancelling"}
FINISHED_STATES = {"complete", "failed", "verification_failed", "cancelled"}


# ---------------------------------------------------------------------------
# APP / MODELS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Rip Node API",
    version="0.2.8",
    description=f"Backend API for {NODE_NAME}",
)


class RipRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    year: Optional[int] = Field(default=None, ge=1880, le=2200)
    season: Optional[int] = Field(default=None, ge=0, le=999)
    disc: Optional[int] = Field(default=None, ge=0, le=999)
    # Recorded with the job so the node's own /jobs output stays meaningful
    # even if the manager database is rebuilt.
    barcode: Optional[str] = Field(default=None, max_length=64)
    media_type: Optional[str] = Field(default=None, pattern="^(movie|tv|music|audiobook)$")
    # Album artist for music, author for audiobooks.
    creator: Optional[str] = Field(default=None, max_length=160)
    narrator: Optional[str] = Field(default=None, max_length=160)
    # Lets the manager's Auto-eject setting actually reach the node instead of
    # every node silently using its own environment variable.
    auto_eject: Optional[bool] = None


class DriveMapRequest(BaseModel):
    device: str = Field(min_length=4, max_length=200)
    swap: bool = True


class StoragePathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)



class Job:
    def __init__(self, drive: str, output_dir: Path, command: list[str], request: RipRequest,
                 engine: str = "makemkv", temp_paths: Optional[list[Path]] = None):
        self.id = str(uuid.uuid4())
        self.drive = drive
        self.output_dir = output_dir
        self.command = command
        self.request = request
        self.engine = engine
        self.temp_paths = temp_paths or []
        self.auto_eject = AUTO_EJECT_AFTER_RIP if request.auto_eject is None else request.auto_eject
        self.state = "starting"
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.return_code: Optional[int] = None
        self.progress: Optional[float] = None
        self.progress_raw_current: Optional[int] = None
        self.progress_raw_total: Optional[int] = None
        self.progress_raw_max: Optional[int] = None
        self.current_operation: Optional[str] = None
        self.copying_started = False
        self.replacement_backup: Optional[Path] = None
        self.replaced_existing = False
        self.verification: Optional[dict] = None
        self.last_message = ""
        self.process: Optional[subprocess.Popen] = None
        self.log: list[str] = []

        # Live diagnostics used by the web manager.
        now = time.time()
        self.last_activity_at = now
        self.last_progress_at = now
        self.last_output_growth_at = now
        self.last_output_bytes = 0
        self.warning_count = 0
        self.last_warning = ""
        self.last_warning_at: Optional[float] = None

    def health_dict(self) -> dict:
        """Return non-blocking live rip-health information for Rip Manager."""
        now = time.time()
        process_running = False
        pid = None
        process_cpu_percent = None
        process_rss_bytes = None

        if self.process is not None:
            pid = self.process.pid
            process_running = self.process.poll() is None
            if process_running:
                try:
                    proc = psutil.Process(pid)
                    process_cpu_percent = proc.cpu_percent(interval=None)
                    process_rss_bytes = proc.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    pass

        output_bytes = directory_size(self.output_dir)
        if output_bytes > self.last_output_bytes:
            self.last_output_bytes = output_bytes
            self.last_output_growth_at = now

        progress_age = max(0.0, now - self.last_progress_at)
        activity_age = max(0.0, now - self.last_activity_at)
        output_age = max(0.0, now - self.last_output_growth_at)

        if self.state == "verifying":
            state = "verifying"
            label = "Verifying"
        elif self.state not in {"starting", "ripping", "cancelling"}:
            state = "stopped"
            label = "Stopped"
        elif not process_running and self.state != "starting":
            state = "stopped"
            label = "Stopped"
        else:
            effective_age = min(progress_age, activity_age, output_age)
            if effective_age >= RIP_HEALTH_STALL_SECONDS:
                state = "stalled"
                label = "Possibly stalled"
            elif effective_age >= RIP_HEALTH_SLOW_SECONDS:
                state = "slow"
                label = "Slow / retrying"
            else:
                state = "active"
                label = "Active"

        return {
            "state": state,
            "label": label,
            "process_running": process_running,
            "pid": pid,
            "process_cpu_percent": process_cpu_percent,
            "process_rss_bytes": process_rss_bytes,
            "seconds_since_progress": round(progress_age, 1),
            "seconds_since_activity": round(activity_age, 1),
            "seconds_since_output_growth": round(output_age, 1),
            "output_bytes": output_bytes,
            "warning_count": self.warning_count,
            "last_warning": self.last_warning or None,
            "last_warning_at": self.last_warning_at,
            "slow_after_seconds": RIP_HEALTH_SLOW_SECONDS,
            "stalled_after_seconds": RIP_HEALTH_STALL_SECONDS,
        }

    def as_dict(self):
        return {
            "id": self.id,
            "drive": self.drive,
            "engine": self.engine,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "return_code": self.return_code,
            "progress": self.progress,
            "progress_raw": {
                "current": self.progress_raw_current,
                "total": self.progress_raw_total,
                "max": self.progress_raw_max,
            },
            "current_operation": self.current_operation,
            "verification": self.verification,
            "last_message": self.last_message,
            "elapsed_seconds": round((self.finished_at or time.time()) - self.started_at, 1),
            "output_dir": str(self.output_dir),
            "output_bytes": directory_size(self.output_dir),
            "replaced_existing": self.replaced_existing,
            "health": self.health_dict(),
            "media": {
                "title": self.request.title,
                "year": self.request.year,
                "season": self.request.season,
                "disc": self.request.disc,
                "barcode": self.request.barcode,
                "media_type": self.request.media_type,
                "creator": self.request.creator,
                "narrator": self.request.narrator,
            },
        }


jobs: Dict[str, Job] = {}
drive_active_job: Dict[str, str] = {}
active_output_dirs: Dict[str, str] = {}
lock = threading.Lock()
media_cache: Dict[str, dict] = {}
media_cache_lock = threading.Lock()
udev_cache: Dict[str, dict] = {}
udev_cache_lock = threading.Lock()
stats_cache: Dict[str, object] = {}
stats_cache_lock = threading.Lock()
drive_speed_samples: Dict[str, tuple[float, int]] = {}

# Per-drive clean-state tracking. Once media is removed, the node intentionally
# exposes no stale disc/job state. Only these four previous-disc fields remain.
drive_runtime: Dict[str, dict] = {
    name: {"last_present": False, "previous_disc": None, "last_job_id": None}
    for name in DRIVE_MAP
}
drive_runtime_lock = threading.Lock()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def require_token(authorization: Optional[str]) -> None:
    if not API_TOKEN:
        return
    expected = f"Bearer {API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid API token")


def refresh_optical_devices() -> None:
    """Discard stale udev facts and wait briefly for hot-plug events."""
    with udev_cache_lock:
        udev_cache.clear()
    try:
        run_command(["udevadm", "settle", "--timeout=5"], timeout=6)
    except (OSError, subprocess.SubprocessError):
        pass


def scan_optical_devices() -> list[dict]:
    """Return physical optical drives currently visible to Linux."""
    found = []
    names = {path.name for path in Path("/dev").glob("sr*")}
    names.update(path.name for path in Path("/sys/class/block").glob("sr*"))
    for name in sorted(names, key=lambda value: (int(value[2:]) if value[2:].isdigit() else 9999, value)):
        device = f"/dev/{name}"
        if not device_exists(device):
            continue
        props = get_udev_properties(device)
        found.append({
            "device": device,
            "exists": device_exists(device),
            "model": props.get("ID_MODEL_FROM_DATABASE") or props.get("ID_MODEL"),
            "vendor": props.get("ID_VENDOR"),
            "serial": props.get("ID_SERIAL_SHORT") or props.get("ID_SERIAL"),
            "id_path": props.get("ID_PATH"),
        })
    return found


def resolve_mapping_entry(entry: dict) -> str:
    """Resolve a slot to the current /dev/srX, preferring persistent ID_PATH."""
    wanted_path = entry.get("id_path")
    if wanted_path:
        for item in scan_optical_devices():
            if item.get("id_path") == wanted_path:
                return item["device"]
    configured = str(entry.get("device") or "")
    if configured and device_exists(configured):
        return configured
    # Keep the configured path for useful "missing" status reporting.
    return configured


def get_device(drive_name: str) -> str:
    drive_name = drive_name.upper()
    with drive_map_lock:
        entry = DRIVE_MAP.get(drive_name)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Unknown drive: {drive_name}")
        return resolve_mapping_entry(dict(entry))


def run_command(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def safe_name(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:160] or "Untitled"


def sanitize_name(value: str) -> str:
    """Return a filesystem-safe title while retaining normal spaces."""
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", value)
    value = re.sub(r"\s+", " ", value).strip().rstrip(".")
    return value or "Untitled"


def build_output_name(req: RipRequest) -> Path:
    """
    Build the output path.

    Movies:
        /mnt/ripping/Movies/Title (Year)

    TV / seasonal media:
        /mnt/ripping/TV/Title (Year)/Season #/Disk #

    Music:
        /mnt/ripping/Music/Album Artist/Album (Year)/Disc #

    Audiobooks:
        /mnt/ripping/AudioBooks/Author/Book (Year) {Narrator}/Disc #

    Season and disc deliberately use human-readable folder names because this
    is the staging/ripping structure requested for the Rip Remote workflow.
    """
    title = sanitize_name(req.title.strip())
    if req.year:
        title = f"{title} ({req.year})"

    media_type = req.media_type or "movie"
    if media_type == "tv":
        base = Path("TV") / title
        if req.season is not None:
            base = base / f"Season {req.season}"
        if req.disc is not None:
            base = base / f"Disk {req.disc}"
    elif media_type in {"music", "audiobook"}:
        creator = sanitize_name((req.creator or "Unknown Artist" if media_type == "music"
                                 else req.creator or "Unknown Author").strip())
        if media_type == "audiobook" and req.narrator:
            title = f"{title} {{{sanitize_name(req.narrator.strip())}}}"
        root = "Music" if media_type == "music" else "AudioBooks"
        base = Path(root) / creator / title / f"Disc {req.disc or 1}"
    else:
        base = Path("Movies") / title
        if req.disc is not None:
            base = base / f"Disk {req.disc}"

    return base


def build_audio_command(req: RipRequest, raw_device: str, output_dir: Path) -> tuple[list[str], list[Path]]:
    """Create an isolated, non-interactive abcde configuration for one job."""
    token = uuid.uuid4().hex
    config_path = Path("/tmp") / f"rip-node-abcde-{token}.conf"
    wav_dir = Path("/tmp") / f"rip-node-audio-{token}"
    output_type = "flac" if req.media_type == "music" else "mp3"
    config = "\n".join([
        f"OUTPUTDIR={shlex.quote(str(output_dir))}",
        f"WAVOUTPUTDIR={shlex.quote(str(wav_dir))}",
        "OUTPUTFORMAT='${TRACKNUM} - ${TRACKFILE}'",
        "VAOUTPUTFORMAT='${TRACKNUM} - ${TRACKFILE}'",
        "CDDBMETHOD=musicbrainz,cdtext",
        "INTERACTIVE=n",
        "NOSUBMIT=y",
        "PADTRACKS=y",
        "EJECTCD=n",
        "KEEPWAVS=n",
        "MAXPROCS=2",
        f"OUTPUTTYPE={output_type}",
        "LAMEOPTS='-V 4'",
        "",
    ])
    config_path.write_text(config, encoding="utf-8")
    command = [
        "abcde", "-c", str(config_path), "-d", raw_device, "-N",
        "-W", str(req.disc or 1), "-o", output_type,
        "-a", "cddb,read,encode,tag,move,clean",
    ]
    return command, [config_path, wav_dir]


def device_exists(device: str) -> bool:
    return Path(device).exists()


def resolve_device(device: str) -> str:
    """Resolve /dev/ripper/* symlinks to the real /dev/srX path for MakeMKV."""
    try:
        return str(Path(device).resolve(strict=True))
    except FileNotFoundError:
        return device


def ensure_makemkv_settings() -> None:
    """
    Ensure headless MakeMKV uses the intended language/track-selection defaults.
    makemkvcon reads ~/.MakeMKV/settings.conf for the user running the API.
    Existing unrelated MakeMKV settings (including a licence key) are preserved.
    """
    settings_dir = Path.home() / ".MakeMKV"
    settings_file = settings_dir / "settings.conf"
    settings_dir.mkdir(parents=True, exist_ok=True)

    existing = settings_file.read_text(encoding="utf-8") if settings_file.exists() else ""
    lines = existing.splitlines()

    wanted = {
        "app_PreferredLanguage": MAKEMKV_PREFERRED_LANGUAGE,
        "app_DefaultSelectionString": MAKEMKV_DEFAULT_SELECTION,
        "app_ExpertMode": "1",
    }

    output = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        replaced = False
        for key, value in wanted.items():
            if stripped.startswith(key + " ") or stripped.startswith(key + "="):
                output.append(f'{key} = "{value}"')
                seen.add(key)
                replaced = True
                break
        if not replaced:
            output.append(line)

    for key, value in wanted.items():
        if key not in seen:
            output.append(f'{key} = "{value}"')

    settings_file.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def get_udev_properties(device: str) -> dict:
    """
    Cached udev lookup.

    Model, vendor and serial cannot change while the drive stays plugged in,
    so running udevadm on every status request only added a subprocess to
    every poll. A missing device clears the entry so a hot-plugged drive is
    picked up again.
    """
    if not device_exists(device):
        with udev_cache_lock:
            udev_cache.pop(device, None)
        return {}

    now = time.time()
    with udev_cache_lock:
        cached = udev_cache.get(device)
        if cached and now - cached["read_at"] <= UDEV_CACHE_SECONDS and cached["props"]:
            return cached["props"]

    try:
        result = run_command(["udevadm", "info", "--query=property", f"--name={device}"], timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}

    props = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value

    with udev_cache_lock:
        udev_cache[device] = {"read_at": now, "props": props}
    return props


# ---------------------------------------------------------------------------
# TRAY AND MEDIA DETECTION
# ---------------------------------------------------------------------------

# linux/cdrom.h
CDROM_DRIVE_STATUS = 0x5326
CDSL_CURRENT = 0x7FFFFFFF
CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4

TRAY_STATES = {
    CDS_NO_INFO: "unknown",
    CDS_NO_DISC: "empty",
    CDS_TRAY_OPEN: "open",
    CDS_DRIVE_NOT_READY: "loading",
    CDS_DISC_OK: "disc",
}


def read_tray_state(device: str) -> str:
    """
    Ask the kernel directly whether the tray is open, empty, still spinning up
    or holding a readable disc.

    This is the piece that was missing: blkid cannot tell an open tray apart
    from a closed empty drive, so both looked identical to the manager. The
    ioctl answers in microseconds and never spins the disc up, which is why it
    is now the first probe rather than blkid.
    """
    fd = None
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        status = fcntl.ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)
        return TRAY_STATES.get(status, "unknown")
    except (OSError, TypeError):
        return "unknown"
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def read_disc_label(raw_device: str, device: str) -> dict:
    """
    Best-effort filesystem details for a disc that is already known to be
    loaded and readable.

    A slow/unresponsive optical drive must NEVER make /drives return HTTP 500,
    so a timeout falls back to the last good reading. Media status is
    advisory; MakeMKV remains the authority when a rip starts.
    """
    try:
        blkid = run_command(["blkid", "-o", "export", raw_device], timeout=MEDIA_PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        with media_cache_lock:
            cached = media_cache.get(device)
        if cached and time.time() - cached["detected_at"] <= MEDIA_CACHE_GRACE:
            return {
                **cached["media"],
                "reason": "detected_cached",
                "stale": True,
                "status_message": "The drive is still reading the disc",
                "last_detected_at": cached["detected_at"],
            }
        return {"present": True, "reason": "probe_timeout", "device": raw_device,
                "status_message": "The drive is still reading the disc"}
    except (FileNotFoundError, OSError) as exc:
        return {"present": True, "reason": "probe_error", "device": raw_device, "error": str(exc)}

    if blkid.returncode != 0:
        # A disc is loaded but carries no recognised filesystem. That is normal
        # for some copy-protected video discs, so it is reported as present.
        return {
            "present": True,
            "reason": "unreadable_filesystem",
            "device": raw_device,
            "status_message": "A disc is loaded but its filesystem could not be identified",
        }

    fs = {}
    for line in blkid.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fs[k] = v

    media = {
        "present": True,
        "reason": "detected",
        "device": raw_device,
        "label": fs.get("LABEL"),
        "type": fs.get("TYPE"),
        "uuid": fs.get("UUID"),
    }
    with media_cache_lock:
        media_cache[device] = {"detected_at": time.time(), "media": media}
    return media


def read_disc_status(device: str) -> dict:
    """Tray state first, filesystem probe only when a disc is actually there."""
    if not device_exists(device):
        with media_cache_lock:
            media_cache.pop(device, None)
        return {"present": False, "tray": "unknown", "reason": "device_missing"}

    raw_device = resolve_device(device)
    tray = read_tray_state(raw_device)

    if tray == "open":
        with media_cache_lock:
            media_cache.pop(device, None)
        return {
            "present": False,
            "tray": "open",
            "device": raw_device,
            "reason": "tray_open",
            "status_message": "The tray is open",
        }

    if tray == "empty":
        with media_cache_lock:
            media_cache.pop(device, None)
        return {
            "present": False,
            "tray": "empty",
            "device": raw_device,
            "reason": "no_readable_media",
            "status_message": "The drive is empty",
        }

    if tray == "loading":
        return {
            "present": False,
            "tray": "loading",
            "device": raw_device,
            "reason": "drive_loading",
            "status_message": "The drive is still reading the disc",
        }

    if tray == "disc":
        return {**read_disc_label(raw_device, device), "tray": "disc"}

    # The ioctl is unavailable (unusual kernel, container, or a device that is
    # not a real optical drive). Fall back to the original blkid behaviour so
    # nothing regresses on hardware where the tray cannot be queried.
    fallback = read_disc_label(raw_device, device)
    if fallback.get("reason") in {"unreadable_filesystem", "probe_error"}:
        return {"present": False, "tray": "unknown", "device": raw_device,
                "reason": "no_readable_media", "status_message": "No readable disc detected"}
    return {**fallback, "tray": "unknown"}


def remember_previous_disc(drive_name: str) -> None:
    """Retain only title/year/season/disc from the latest job on this drive."""
    name = drive_name.upper()
    with drive_runtime_lock:
        job_id = drive_runtime.setdefault(
            name, {"last_present": False, "previous_disc": None, "last_job_id": None}
        ).get("last_job_id")

    job = jobs.get(job_id) if job_id else None
    if job is None:
        candidates = [item for item in jobs.values() if item.drive == name]
        if candidates:
            job = max(candidates, key=lambda item: item.started_at)

    if job is None:
        return

    previous = {
        "title": job.request.title,
        "year": job.request.year,
        "season": job.request.season,
        "disc": job.request.disc,
    }
    with drive_runtime_lock:
        drive_runtime[name]["previous_disc"] = previous


def update_drive_lifecycle(drive_name: str, media: dict, active: Optional[Job]) -> None:
    """Detect actual media removal and transition the drive to a clean state."""
    name = drive_name.upper()
    present = bool(media.get("present"))
    with drive_runtime_lock:
        runtime = drive_runtime.setdefault(
            name, {"last_present": False, "previous_disc": None, "last_job_id": None}
        )
        was_present = bool(runtime.get("last_present"))

    if was_present and not present and active is None:
        remember_previous_disc(name)

    with drive_runtime_lock:
        drive_runtime[name]["last_present"] = present


def get_drive_status(drive_name: str) -> dict:
    name = drive_name.upper()
    device = get_device(name)
    props = get_udev_properties(device)

    with lock:
        job_id = drive_active_job.get(name)
        active = jobs.get(job_id) if job_id else None

    if active and active.state in ACTIVE_STATES:
        with media_cache_lock:
            cached = media_cache.get(device)
        media = dict(cached["media"]) if cached else {
            "present": True,
            "device": resolve_device(device),
        }
        media.update({
            "present": True,
            "tray": "disc",
            "reason": "being_ripped",
            "status_message": "The disc is currently being ripped",
        })
    else:
        media = read_disc_status(device)

    update_drive_lifecycle(name, media, active)

    with drive_runtime_lock:
        previous_disc = drive_runtime.get(name, {}).get("previous_disc")

    clean_state = not bool(media.get("present")) and active is None

    return {
        "name": name,
        "device": device,
        "exists": device_exists(device),
        "model": props.get("ID_MODEL_FROM_DATABASE") or props.get("ID_MODEL"),
        "vendor": props.get("ID_VENDOR"),
        "serial": props.get("ID_SERIAL_SHORT") or props.get("ID_SERIAL"),
        "path": props.get("ID_PATH"),
        "tray": media.get("tray", "unknown"),
        "media": media,
        "active_job": None if clean_state else (active.as_dict() if active else None),
        "previous_disc": previous_disc,
        "clean_state": clean_state,
    }


# ---------------------------------------------------------------------------
# SOUNDS, VERIFICATION AND DISC CONTROL
# ---------------------------------------------------------------------------

def play_sound(path: str) -> None:
    """Play an optional MP3 or WAV without blocking the API."""
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    player = ["mpg123", "-q", str(p)] if p.suffix.lower() == ".mp3" else ["aplay", "-q", str(p)]
    if not shutil.which(player[0]):
        return
    try:
        subprocess.Popen(
            player,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def verify_rip(output_dir: Path) -> dict:
    """
    Verify every MKV produced by the job.

    This is deliberately stricter than merely trusting MakeMKV's exit code:
    - at least one MKV must exist
    - each MKV must be larger than the configured minimum
    - ffprobe must successfully open it
    - ffprobe must report at least one video stream

    This verifies container readability and expected output structure. It is not
    a second bit-for-bit reread of the optical disc.
    """
    mkvs = sorted(output_dir.rglob("*.mkv"))
    result = {
        "ok": False,
        "files_checked": len(mkvs),
        "files": [],
        "error": None,
    }

    if not mkvs:
        result["error"] = "No MKV files were produced"
        return result

    for mkv in mkvs:
        try:
            size = mkv.stat().st_size
        except OSError as exc:
            result["error"] = f"Could not stat {mkv.name}: {exc}"
            return result

        item = {
            "file": str(mkv),
            "bytes": size,
            "ffprobe_ok": False,
            "video_streams": 0,
        }

        if size < MIN_VERIFIED_MKV_BYTES:
            result["files"].append(item)
            result["error"] = f"{mkv.name} is unexpectedly small ({size} bytes)"
            return result

        try:
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-select_streams", "v",
                    "-show_entries", "stream=index",
                    "-of", "csv=p=0",
                    str(mkv),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
        except FileNotFoundError:
            result["error"] = "ffprobe is not installed"
            result["files"].append(item)
            return result
        except subprocess.TimeoutExpired:
            result["error"] = f"ffprobe timed out on {mkv.name}"
            result["files"].append(item)
            return result

        streams = [line for line in probe.stdout.splitlines() if line.strip()]
        item["ffprobe_ok"] = probe.returncode == 0
        item["video_streams"] = len(streams)
        result["files"].append(item)

        if probe.returncode != 0:
            result["error"] = f"ffprobe could not read {mkv.name}: {probe.stderr.strip()}"
            return result

        if not streams:
            result["error"] = f"No video stream found in {mkv.name}"
            return result

    result["ok"] = True
    return result


def verify_audio(output_dir: Path, media_type: str) -> dict:
    """Verify abcde output contains readable files with at least one audio stream."""
    extensions = {".flac"} if media_type == "music" else {".mp3"}
    files = sorted(path for path in output_dir.rglob("*") if path.suffix.lower() in extensions)
    result = {"ok": False, "files_checked": len(files), "files": [], "error": None}
    if not files:
        result["error"] = f"No {'FLAC' if media_type == 'music' else 'MP3'} audio files were produced"
        return result

    for audio_file in files:
        try:
            size = audio_file.stat().st_size
        except OSError as exc:
            result["error"] = f"Could not stat {audio_file.name}: {exc}"
            return result
        item = {"file": str(audio_file), "bytes": size, "ffprobe_ok": False, "audio_streams": 0}
        result["files"].append(item)
        if size < MIN_VERIFIED_AUDIO_BYTES:
            result["error"] = f"{audio_file.name} is unexpectedly small ({size} bytes)"
            return result
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", str(audio_file)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60, check=False,
            )
        except FileNotFoundError:
            result["error"] = "ffprobe is not installed"
            return result
        except subprocess.TimeoutExpired:
            result["error"] = f"ffprobe timed out on {audio_file.name}"
            return result
        streams = [line for line in probe.stdout.splitlines() if line.strip()]
        item["ffprobe_ok"] = probe.returncode == 0
        item["audio_streams"] = len(streams)
        if probe.returncode != 0 or not streams:
            result["error"] = (f"ffprobe could not read {audio_file.name}: {probe.stderr.strip()}"
                               if probe.returncode else f"No audio stream found in {audio_file.name}")
            return result
    result["ok"] = True
    return result


def verify_job(job: Job) -> dict:
    media_type = job.request.media_type or "movie"
    return verify_audio(job.output_dir, media_type) if media_type in {"music", "audiobook"} else verify_rip(job.output_dir)


def eject_drive(device: str) -> None:
    result = run_command(["eject", device])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Eject failed")
    with media_cache_lock:
        media_cache.pop(device, None)


def close_drive(device: str) -> None:
    result = run_command(["eject", "-t", device])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Close tray failed")
    with media_cache_lock:
        media_cache.pop(device, None)


def directory_size(path: Path) -> int:
    """Best-effort recursive size of a job output folder."""
    if not path.exists():
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except (FileNotFoundError, PermissionError, OSError):
                pass
    return total


def collect_drive_speeds(now: float) -> list[dict]:
    """Measure real rip throughput from growth of each job's output folder."""
    with lock:
        active_by_drive = {
            drive: jobs.get(job_id)
            for drive, job_id in drive_active_job.items()
        }

    rows = []
    active_job_ids = set()
    for drive_name, device in DRIVE_MAP.items():
        job = active_by_drive.get(drive_name.upper())
        state = job.state if job else "idle"
        bytes_per_second = None
        speed_x = None
        output_bytes = 0
        engine = job.engine if job else None

        if job and job.state in ACTIVE_STATES:
            active_job_ids.add(job.id)
            output_bytes = directory_size(job.output_dir)
            previous = drive_speed_samples.get(job.id)
            if previous:
                elapsed = now - previous[0]
                byte_growth = output_bytes - previous[1]
                if elapsed >= 0.5 and byte_growth >= 0:
                    bytes_per_second = byte_growth / elapsed
                    # DVD 1x is 1.385 MB/s; audio CD 1x is 176.4 kB/s.
                    one_x = 1_385_000 if job.engine == "makemkv" else 176_400
                    speed_x = bytes_per_second / one_x
            drive_speed_samples[job.id] = (now, output_bytes)

        rows.append({
            "name": drive_name.upper(),
            "device": device,
            "state": state,
            "engine": engine,
            "media": "DVD" if engine == "makemkv" else "CD" if engine else None,
            "bytes_per_second": round(bytes_per_second) if bytes_per_second is not None else None,
            "megabits_per_second": round(bytes_per_second * 8 / 1_000_000, 1)
                if bytes_per_second is not None else None,
            "speed_x": round(speed_x, 1) if speed_x is not None else None,
            "output_bytes": output_bytes,
        })

    for job_id in list(drive_speed_samples):
        if job_id not in active_job_ids:
            drive_speed_samples.pop(job_id, None)
    return rows


# ---------------------------------------------------------------------------
# RIP EXECUTION
# ---------------------------------------------------------------------------

def parse_robot_message(line: str, job: Job) -> None:
    """Parse MakeMKV progress and record enough activity to diagnose stalls."""
    now = time.time()
    if line.strip():
        job.last_activity_at = now

    if line.startswith("PRGV:"):
        try:
            values = line[5:].split(",")
            current = int(values[0])
            total = int(values[1])
            max_value = int(values[2]) if len(values) > 2 else 0
            job.progress_raw_current = current
            job.progress_raw_total = total
            job.progress_raw_max = max_value
            if max_value > 0 and job.copying_started:
                pct = round(max(0.0, min(99.9, (total / max_value) * 100.0)), 1)
                if job.progress is None or pct != job.progress:
                    job.progress = pct
                    job.last_progress_at = now
        except (ValueError, IndexError):
            pass

    elif line.startswith("PRGC:"):
        quoted = re.findall(r'"([^"]+)"', line)
        job.current_operation = quoted[-1] if quoted else "Reading and copying the disc"
        operation = job.current_operation.lower()
        if not job.copying_started and ("saving to mkv" in operation or "copying" in operation):
            job.copying_started = True
            job.progress = 0.0
            job.last_progress_at = now

    elif line.startswith("MSG:"):
        quoted = re.findall(r'"([^"]+)"', line)
        if quoted:
            message = quoted[0].replace("%1", "the output folder")
            job.last_message = message
            lower = message.lower()
            warning_terms = (
                "av sync issue",
                "audio gap",
                "missing frame",
                "read error",
                "corrupt",
                "retry",
            )
            if any(term in lower for term in warning_terms):
                job.warning_count += 1
                job.last_warning = message
                job.last_warning_at = now


def parse_audio_message(line: str, job: Job) -> None:
    """Turn abcde output into progress while tracking live activity."""
    now = time.time()
    if line.strip():
        job.last_activity_at = now

    match = re.search(r"(?:track|Track)\s+(\d+)\s+(?:of|/)\s*(\d+)", line)
    if match:
        current, total = int(match.group(1)), int(match.group(2))
        if total > 0:
            job.copying_started = True
            job.progress_raw_current = current
            job.progress_raw_total = total
            job.progress_raw_max = total
            pct = round(max(0.0, min(95.0, ((current - 1) / total) * 95.0)), 1)
            if job.progress is None or pct != job.progress:
                job.progress = pct
                job.last_progress_at = now
            job.current_operation = f"Ripping audio track {current} of {total}"

    lower = line.lower()
    if "encoding" in lower:
        job.current_operation = "Encoding audio tracks"
    elif "tagging" in lower:
        job.current_operation = "Tagging audio tracks"
    elif "musicbrainz" in lower or "cddb" in lower:
        job.current_operation = "Looking up disc metadata"
    if line.strip():
        job.last_message = line.strip()[-500:]


def prepare_replacement(job: Job) -> None:
    """Move an existing destination aside before writing the replacement."""
    output_dir = job.output_dir
    if not output_dir.exists() or not any(output_dir.iterdir()):
        output_dir.mkdir(parents=True, exist_ok=True)
        return

    backup_root = RIP_ROOT / ".replacement-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = backup_root / job.id
    output_dir.rename(backup)
    job.replacement_backup = backup
    job.replaced_existing = True
    output_dir.mkdir(parents=True, exist_ok=True)


def finish_replacement(job: Job) -> None:
    """Discard the old destination only after the new rip verifies."""
    backup = job.replacement_backup
    if not backup:
        return
    try:
        shutil.rmtree(backup)
        job.replacement_backup = None
    except OSError as exc:
        job.last_message = f"Rip verified; old replacement backup could not be removed: {exc}"


def roll_back_replacement(job: Job) -> None:
    """Restore the original destination when its replacement does not verify."""
    backup = job.replacement_backup
    if not backup or not backup.exists():
        return
    try:
        if job.output_dir.exists():
            shutil.rmtree(job.output_dir)
        backup.rename(job.output_dir)
        job.replacement_backup = None
        if job.last_message:
            job.last_message += "; the original files were restored"
        else:
            job.last_message = "The new rip failed and the original files were restored"
    except OSError as exc:
        job.last_message = f"Replacement failed and the original files could not be restored automatically: {exc}"


def prune_jobs() -> None:
    """Keep recent history without letting a long-lived node leak memory."""
    with lock:
        finished = sorted(
            (j for j in jobs.values() if j.state in FINISHED_STATES or j.state == "interrupted"),
            key=lambda j: j.finished_at or j.started_at,
        )
        for job in finished[:max(0, len(finished) - MAX_FINISHED_JOBS)]:
            jobs.pop(job.id, None)


def ensure_output_roots() -> None:
    """Create the four user-visible library roots beneath the Rips mount."""
    for name in ("Movies", "TV", "Music", "AudioBooks"):
        (RIP_ROOT / name).mkdir(parents=True, exist_ok=True)


def run_rip(job: Job) -> None:
    drive = job.drive
    try:
        prepare_replacement(job)
        job.state = "ripping"
        job.last_activity_at = time.time()

        proc = subprocess.Popen(
            job.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        job.process = proc
        try:
            psutil.Process(proc.pid).cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            job.log.append(line)
            if len(job.log) > 500:
                job.log = job.log[-500:]

            if job.engine == "makemkv":
                parse_robot_message(line, job)
            else:
                parse_audio_message(line, job)
            if job.progress is not None and not job.last_message:
                job.last_message = f"Ripping disc — {job.progress:.1f}% complete"

        rc = proc.wait()
        job.return_code = rc
        job.finished_at = time.time()
        job.last_activity_at = job.finished_at

        if job.state == "cancelling":
            job.state = "cancelled"
        elif rc == 0:
            job.state = "verifying"
            job.current_operation = "Verifying output"
            job.progress = 99.9
            job.last_progress_at = time.time()
            job.verification = verify_job(job)

            if job.verification.get("ok"):
                job.progress = 100.0
                job.state = "complete"
                job.current_operation = "Complete"
                job.last_message = f"{job.request.media_type or 'movie'} rip completed and output verified"
                finish_replacement(job)
                play_sound(COMPLETE_SOUND)

                with drive_runtime_lock:
                    drive_runtime.setdefault(
                        drive,
                        {"last_present": True, "previous_disc": None, "last_job_id": None},
                    )["last_job_id"] = job.id

                if job.auto_eject:
                    try:
                        eject_drive(get_device(drive))
                    except Exception as exc:
                        job.last_message = f"Rip verified; auto-eject failed: {exc}"
                        play_sound(NEEDS_ATTENTION_SOUND)
            else:
                job.state = "verification_failed"
                job.current_operation = "Verification failed"
                job.last_message = job.verification.get("error") or "Verification failed"
                play_sound(VERIFY_FAIL_SOUND)
        else:
            job.state = "failed"
            job.current_operation = "Rip failed"
            play_sound(FAIL_SOUND)

        if job.state in {"failed", "verification_failed", "cancelled"}:
            roll_back_replacement(job)

    except Exception as exc:
        job.state = "failed"
        job.finished_at = time.time()
        job.current_operation = "Backend/rip error"
        job.last_message = str(exc)
        roll_back_replacement(job)
        play_sound(FAIL_SOUND)

    finally:
        for temp_path in job.temp_paths:
            try:
                if temp_path.is_dir():
                    shutil.rmtree(temp_path)
                elif temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
        with lock:
            if drive_active_job.get(drive) == job.id:
                drive_active_job.pop(drive, None)
            output_key = str(job.output_dir.resolve(strict=False))
            if active_output_dirs.get(output_key) == job.id:
                active_output_dirs.pop(output_key, None)
        prune_jobs()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "rip-node-api",
        "node": NODE_NAME,
        "version": app.version,
        "docs": "/docs",
        "features": {
            "rip_health": True,
            "clean_drive_reset": True,
            "previous_disc_reference": True,
            "optical_drive_speed": True,
        },
        "engines": {
            "video": {"name": "MakeMKV", "available": bool(shutil.which("makemkvcon"))},
            "audio": {
                "name": "abcde",
                "available": all(shutil.which(item) for item in ("abcde", "cdparanoia", "ffprobe")),
                "music_encoder": {"name": "FLAC", "available": bool(shutil.which("flac"))},
                "audiobook_encoder": {"name": "LAME MP3", "available": bool(shutil.which("lame"))},
            },
        },
    }


@app.get("/api/info")
def api_info():
    """Stable version endpoint used by Rip Manager and automatic updates."""
    return root()


@app.get("/health")
def health():
    return {"ok": True, "node": NODE_NAME, "time": time.time()}


@app.get("/status")
def status(authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    return {
        "node": NODE_NAME,
        "drives": [get_drive_status(name) for name in DRIVE_MAP],
        "active_jobs": [
            job.as_dict()
            for job in jobs.values()
            if job.state in {"starting", "ripping", "verifying", "cancelling"}
        ],
    }



@app.get("/settings/storage")
def get_storage_settings(authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    path = RIP_ROOT
    writable = path.is_dir() and os.access(path, os.W_OK | os.X_OK)
    return {"path": str(path), "exists": path.is_dir(), "writable": writable}


@app.put("/settings/storage")
def set_storage_settings(req: StoragePathRequest, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    global RIP_ROOT
    with lock:
        if drive_active_job:
            raise HTTPException(status_code=409, detail="Storage location cannot be changed while a drive is ripping")

    requested = Path(req.path.strip()).expanduser()
    if not requested.is_absolute():
        raise HTTPException(status_code=422, detail="Storage location must be an absolute path")
    requested = requested.resolve()
    if not requested.is_dir():
        raise HTTPException(status_code=422, detail="Storage location does not exist or is not a directory")
    if not os.access(requested, os.W_OK | os.X_OK):
        raise HTTPException(status_code=422, detail="The Rip Node service user cannot write to this directory")

    probe = requested / f".rip-node-write-test-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Storage write test failed: {exc}") from exc

    settings = load_node_settings()
    settings["output_path"] = str(requested)
    save_node_settings(settings)
    RIP_ROOT = requested
    return {"ok": True, "path": str(RIP_ROOT), "exists": True, "writable": True}


@app.get("/drive-mapping")
def drive_mapping(refresh: bool = False, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    if refresh:
        refresh_optical_devices()
    physical = scan_optical_devices()
    with drive_map_lock:
        mappings = []
        for name, entry in DRIVE_MAP.items():
            resolved = resolve_mapping_entry(dict(entry))
            props = get_udev_properties(resolved) if resolved else {}
            mappings.append({
                "name": name,
                "configured_device": entry.get("device"),
                "device": resolved,
                "id_path": entry.get("id_path") or props.get("ID_PATH"),
                "model": props.get("ID_MODEL_FROM_DATABASE") or props.get("ID_MODEL") or entry.get("model"),
                "vendor": props.get("ID_VENDOR"),
                "serial": props.get("ID_SERIAL_SHORT") or props.get("ID_SERIAL") or entry.get("serial"),
                "exists": bool(resolved and device_exists(resolved)),
                "active": name in drive_active_job,
            })
    assigned_paths = {m.get("id_path") for m in mappings if m.get("id_path")}
    assigned_devices = {m.get("device") for m in mappings if m.get("device")}
    for item in physical:
        item["assigned"] = bool(
            (item.get("id_path") and item.get("id_path") in assigned_paths)
            or item.get("device") in assigned_devices
        )
    return {"node": NODE_NAME, "mappings": mappings, "available": physical}


@app.put("/drive-mapping/{drive_name}")
def set_drive_mapping(
    drive_name: str,
    req: DriveMapRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_token(authorization)
    name = drive_name.upper().strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,19}", name):
        raise HTTPException(status_code=422, detail="Drive name must begin with a letter and contain only letters, numbers, dash or underscore")

    with lock:
        if name in drive_active_job:
            raise HTTPException(status_code=409, detail=f"{name} is currently ripping")

    device = req.device.strip()
    physical = scan_optical_devices()
    selected = next((x for x in physical if x["device"] == device), None)
    if not selected:
        raise HTTPException(status_code=404, detail=f"{device} is not a detected optical drive")

    with drive_map_lock:
        # Find another slot already owning this physical drive.
        owner = None
        for other_name, other_entry in DRIVE_MAP.items():
            if other_name == name:
                continue
            other_resolved = resolve_mapping_entry(dict(other_entry))
            same_path = selected.get("id_path") and other_entry.get("id_path") == selected.get("id_path")
            if same_path or other_resolved == device:
                owner = other_name
                break

        old_target = dict(DRIVE_MAP.get(name) or {})
        if owner:
            with lock:
                if owner in drive_active_job:
                    raise HTTPException(status_code=409, detail=f"{owner} is currently ripping")
            if not req.swap:
                raise HTTPException(status_code=409, detail=f"{device} is already assigned to {owner}")
            # If target already existed, exchange mappings. If target is new,
            # move the physical drive from its old slot to the new slot.
            if old_target.get("device"):
                DRIVE_MAP[owner] = old_target
            else:
                DRIVE_MAP.pop(owner, None)

        DRIVE_MAP[name] = {
            "device": device,
            "id_path": selected.get("id_path"),
            "model": selected.get("model"),
            "serial": selected.get("serial"),
        }
        save_drive_map()

    return {"ok": True, "message": f"{device} assigned to {name}", **drive_mapping(authorization=authorization)}


@app.delete("/drive-mapping/{drive_name}")
def remove_drive_mapping(drive_name: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    name = drive_name.upper().strip()
    with lock:
        if name in drive_active_job:
            raise HTTPException(status_code=409, detail=f"{name} is currently ripping")
    with drive_map_lock:
        if name not in DRIVE_MAP:
            raise HTTPException(status_code=404, detail=f"Unknown drive: {name}")
        DRIVE_MAP.pop(name)
        save_drive_map()
    return {"ok": True, "message": f"{name} removed", **drive_mapping(authorization=authorization)}


@app.get("/drives")
def drives(authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    names = list(DRIVE_MAP)
    # Optical drives can take several seconds to spin up. Probe them in
    # parallel so one slow drive cannot make the whole manager request expire.
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as executor:
        return list(executor.map(get_drive_status, names))


@app.get("/drives/{drive_name}")
def drive(drive_name: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    return get_drive_status(drive_name)


@app.get("/drives/{drive_name}/health")
def drive_health(drive_name: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    name = drive_name.upper()
    get_device(name)

    with lock:
        job_id = drive_active_job.get(name)
        job = jobs.get(job_id) if job_id else None

    if not job:
        return {
            "drive": name,
            "active": False,
            "state": "idle",
            "label": "Idle",
        }

    return {
        "drive": name,
        "active": True,
        "job_id": job.id,
        "progress": job.progress,
        "elapsed_seconds": round(time.time() - job.started_at, 1),
        "current_operation": job.current_operation,
        "last_message": job.last_message,
        "health": job.health_dict(),
    }


@app.post("/drives/{drive_name}/eject")
def eject(drive_name: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    name = drive_name.upper()
    device = get_device(name)

    with lock:
        if name in drive_active_job:
            raise HTTPException(status_code=409, detail="Drive is currently ripping")

    try:
        remember_previous_disc(name)
        eject_drive(device)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"ok": True, "drive": name, "action": "eject"}


@app.post("/drives/{drive_name}/close")
def close(drive_name: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    name = drive_name.upper()
    device = get_device(name)

    with lock:
        if name in drive_active_job:
            raise HTTPException(status_code=409, detail="Drive is currently ripping")

    try:
        close_drive(device)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"ok": True, "drive": name, "action": "close"}


@app.post("/drives/{drive_name}/rip")
def start_rip(
    drive_name: str,
    req: RipRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_token(authorization)

    name = drive_name.upper()
    device = get_device(name)

    if not device_exists(device):
        raise HTTPException(status_code=404, detail=f"{device} does not exist")

    with lock:
        if name in drive_active_job:
            existing = jobs[drive_active_job[name]]
            raise HTTPException(
                status_code=409,
                detail=f"{name} already has active job {existing.id}",
            )

    tray = read_tray_state(resolve_device(device))
    if tray == "open":
        raise HTTPException(status_code=409, detail=f"{name} has its tray open")
    if tray == "empty":
        raise HTTPException(status_code=409, detail=f"{name} does not contain a disc")

    media_type = req.media_type or "movie"
    if media_type in {"music", "audiobook"} and not req.creator:
        raise HTTPException(
            status_code=422,
            detail="Album artist is required for music and author is required for audiobooks",
        )

    try:
        ensure_output_roots()
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"Could not create output folders: {exc}") from exc
    output_dir = RIP_ROOT / build_output_name(req)
    output_key = str(output_dir.resolve(strict=False))
    raw_device = resolve_device(device)
    temp_paths: list[Path] = []
    if media_type in {"music", "audiobook"}:
        missing = [program for program in ("abcde", "cdparanoia", "ffprobe") if not shutil.which(program)]
        encoder = "flac" if media_type == "music" else "lame"
        if not shutil.which(encoder):
            missing.append(encoder)
        if missing:
            raise HTTPException(
                status_code=503,
                detail="Audio ripping dependencies are missing: " + ", ".join(sorted(set(missing))),
            )
        command, temp_paths = build_audio_command(req, raw_device, output_dir)
        engine = "abcde-flac" if media_type == "music" else "abcde-mp3"
    else:
        if not shutil.which("makemkvcon"):
            raise HTTPException(status_code=503, detail="makemkvcon is not installed")
        ensure_makemkv_settings()
        # Keep persistent /dev/ripper/DVD* names in the API, but pass MakeMKV
        # the real /dev/srX device path because it does not accept the symlink.
        command = [
            "makemkvcon", "--robot", "--progress=-stdout",
            "--minlength=" + str(MIN_TITLE_SECONDS), "mkv", f"dev:{raw_device}",
            "all", str(output_dir),
        ]
        engine = "makemkv"

    job = Job(name, output_dir, command, req, engine=engine, temp_paths=temp_paths)

    with lock:
        if name in drive_active_job:
            existing = jobs[drive_active_job[name]]
            raise HTTPException(
                status_code=409,
                detail=f"{name} already has active job {existing.id}",
            )
        if output_key in active_output_dirs:
            raise HTTPException(
                status_code=409,
                detail="Another drive is already ripping to this destination",
            )
        jobs[job.id] = job
        drive_active_job[name] = job.id
        active_output_dirs[output_key] = job.id

    with drive_runtime_lock:
        drive_runtime.setdefault(
            name,
            {"last_present": True, "previous_disc": None, "last_job_id": None},
        )["last_job_id"] = job.id

    thread = threading.Thread(target=run_rip, args=(job,), daemon=True)
    thread.start()

    return {
        "ok": True,
        "job": job.as_dict(),
        "command": command,
    }


@app.post("/drives/{drive_name}/cancel")
def cancel_rip(
    drive_name: str,
    authorization: Optional[str] = Header(default=None),
):
    require_token(authorization)
    name = drive_name.upper()
    get_device(name)  # validates name

    with lock:
        job_id = drive_active_job.get(name)
        if not job_id:
            raise HTTPException(status_code=404, detail="No active rip on this drive")
        job = jobs[job_id]

    proc = job.process
    if not proc or proc.poll() is not None:
        raise HTTPException(status_code=409, detail="Process is not running")

    job.state = "cancelling"

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    return {"ok": True, "job": job.as_dict()}


@app.post("/drives/{drive_name}/clear")
def clear_drive(drive_name: str, authorization: Optional[str] = Header(default=None)):
    """Forget finished jobs for a drive without deleting ripped output files."""
    require_token(authorization)
    name = drive_name.upper()
    get_device(name)
    with lock:
        active_id = drive_active_job.get(name)
        if active_id and jobs.get(active_id) and jobs[active_id].state in ACTIVE_STATES:
            raise HTTPException(status_code=409, detail="Cancel the active rip before clearing the drive")
        remove_ids = [job_id for job_id, job in jobs.items()
                      if job.drive == name and job.state not in ACTIVE_STATES]
        for job_id in remove_ids:
            jobs.pop(job_id, None)
        drive_active_job.pop(name, None)
    with drive_runtime_lock:
        runtime = drive_runtime.setdefault(name, {"last_present": False, "previous_disc": None, "last_job_id": None})
        runtime["last_job_id"] = None
    return {"ok": True, "drive": name, "action": "clear"}


@app.get("/jobs")
def list_jobs(authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    return [j.as_dict() for j in sorted(jobs.values(), key=lambda x: x.started_at, reverse=True)]


@app.get("/jobs/{job_id}")
def get_job(job_id: str, authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job")
    data = job.as_dict()
    data["log_tail"] = job.log[-100:]
    return data


# ---------------------------------------------------------------------------
# SYSTEM STATISTICS
# ---------------------------------------------------------------------------

def collect_stats() -> dict:
    collected_at = time.time()
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cpu_freq = psutil.cpu_freq()
    disk_root = psutil.disk_usage("/")
    disk_rips = psutil.disk_usage(str(RIP_ROOT if RIP_ROOT.exists() else Path("/")))
    net_io = psutil.net_io_counters(pernic=True)
    net_addrs = psutil.net_if_addrs()
    net_stats = psutil.net_if_stats()

    cpu_model = platform.processor() or "Unknown"
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass

    temperatures = {}
    try:
        for sensor, entries in psutil.sensors_temperatures(fahrenheit=False).items():
            temperatures[sensor] = [{
                "label": e.label or sensor,
                "current_c": e.current,
                "high_c": e.high,
                "critical_c": e.critical,
            } for e in entries]
    except (AttributeError, OSError):
        pass

    interfaces = {}
    for nic, counters in net_io.items():
        addresses = []
        for addr in net_addrs.get(nic, []):
            if addr.family in (socket.AF_INET, socket.AF_INET6):
                addresses.append(addr.address)
        stat = net_stats.get(nic)
        interfaces[nic] = {
            "up": stat.isup if stat else None,
            "speed_mbps": stat.speed if stat and stat.speed >= 0 else None,
            "addresses": addresses,
            "bytes_sent": counters.bytes_sent,
            "bytes_recv": counters.bytes_recv,
            "packets_sent": counters.packets_sent,
            "packets_recv": counters.packets_recv,
            "errors_in": counters.errin,
            "errors_out": counters.errout,
            "drops_in": counters.dropin,
            "drops_out": counters.dropout,
        }

    process_rows = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_percent", "cpu_percent"]):
        try:
            info = proc.info
            process_rows.append({
                "pid": info["pid"],
                "name": info["name"],
                "user": info["username"],
                "cpu_percent": round(info["cpu_percent"] or 0.0, 1),
                "memory_percent": round(info["memory_percent"] or 0.0, 2),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    top_processes = sorted(process_rows, key=lambda p: (p["cpu_percent"], p["memory_percent"]), reverse=True)[:10]

    return {
        "node": NODE_NAME,
        "hostname": socket.gethostname(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "uptime_seconds": round(time.time() - psutil.boot_time(), 1),
        "cpu": {
            "model": cpu_model,
            # interval=None measures since the previous call, so the manager's
            # own poll cadence supplies the sample window and nothing blocks.
            "usage_percent": psutil.cpu_percent(interval=None),
            "per_core_percent": psutil.cpu_percent(interval=None, percpu=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "frequency_mhz": {
                "current": round(cpu_freq.current, 1) if cpu_freq else None,
                "min": round(cpu_freq.min, 1) if cpu_freq else None,
                "max": round(cpu_freq.max, 1) if cpu_freq else None,
            },
            "load_average": os.getloadavg(),
        },
        "memory": {"total": vm.total, "available": vm.available, "used": vm.used, "percent": vm.percent},
        "swap": {"total": swap.total, "used": swap.used, "free": swap.free, "percent": swap.percent},
        "disks": {
            "root": {"path": "/", "total": disk_root.total, "used": disk_root.used, "free": disk_root.free, "percent": disk_root.percent},
            "ripping": {"path": str(RIP_ROOT), "total": disk_rips.total, "used": disk_rips.used, "free": disk_rips.free, "percent": disk_rips.percent},
        },
        "optical_drives": collect_drive_speeds(collected_at),
        "network": interfaces,
        "temperatures": temperatures,
        "process_count": len(psutil.pids()),
        "top_processes": top_processes,
    }


@app.get("/system/stats")
def system_stats(authorization: Optional[str] = Header(default=None)):
    require_token(authorization)
    now = time.time()
    with stats_cache_lock:
        collected_at = float(stats_cache.get("collected_at", 0.0))
        payload = stats_cache.get("payload")
        if payload and now - collected_at <= STATS_CACHE_SECONDS:
            return {**payload, "cached": True, "collected_at": collected_at}

    payload = collect_stats()
    with stats_cache_lock:
        stats_cache["collected_at"] = now
        stats_cache["payload"] = payload
    return {**payload, "cached": False, "collected_at": now}


if __name__ == "__main__":
    import uvicorn

    ensure_output_roots()
    ensure_makemkv_settings()
    psutil.cpu_percent(interval=None)  # prime the counter for the first request
    uvicorn.run(app, host=HOST, port=PORT)
