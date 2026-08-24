"""Interactive Rip Node simulator for demonstrations of Rip Manager."""

from __future__ import annotations

import random
import time
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from simulator_catalog import SIMULATED_DISCS

app = FastAPI(title="Rip Node Simulator", version="0.2.5-simulator")
START = time.time()


class RipRequest(BaseModel):
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    disc: Optional[int] = None
    barcode: Optional[str] = None
    media_type: Optional[str] = "movie"
    creator: Optional[str] = None
    narrator: Optional[str] = None
    auto_eject: Optional[bool] = True


def random_disc(drive_kind):
    allowed = {"movie", "tv"} if drive_kind == "bluray" else {"movie", "tv", "music", "audiobook"}
    _, selected = random.choice([(code, item) for code, item in SIMULATED_DISCS.items()
                                 if item["media_type"] in allowed])
    metadata = dict(selected)
    media_type = metadata["media_type"]
    metadata["disc"] = random.randint(1, 4) if media_type == "tv" else (random.randint(1, 2) if random.random() < 0.25 else 1)
    disc_format = "BLU-RAY" if drive_kind == "bluray" else ("AUDIO_CD" if media_type in {"music", "audiobook"} else "DVD")
    label_title = "".join(c if c.isalnum() else "_" for c in metadata["title"].upper()).strip("_")
    label = f"{label_title}_DISC_{metadata['disc']}"
    return {"present": True, "tray": "disc", "label": label, "format": disc_format,
            "disc_cycle_id": str(uuid.uuid4()),
            "reason": "ready", "status_message": "Disc detected"}


def drive(name, kind, tray="empty", label=None):
    bluray = kind == "bluray"
    return {
        "name": name, "device": f"/dev/ripper/{name}", "exists": True,
        "model": "ASUS BW-16D1HT Blu-ray" if bluray else "ASUS DRW-24D5MT DVD",
        "vendor": "ASUS", "serial": f"SIM-{name}", "drive_type": "Blu-ray" if bluray else "DVD",
        "path": f"pci-sim-usb-{name[-1]}", "tray": tray,
        "media": {"present": tray == "disc", "tray": tray, "label": label,
                  "format": "BLU-RAY" if bluray else "DVD",
                  "reason": "ready" if tray == "disc" else "no_media",
                  "status_message": "Disc is ready" if tray == "disc" else "No disc inserted"},
        "active_job": None, "previous_disc": None, "clean_state": tray in {"empty", "open"},
    }


drives = {}
jobs = {}


def new_job(name, request, progress=0.0, state="ripping", age=0):
    job_id = str(uuid.uuid4())
    now = time.time()
    media = request.model_dump() if hasattr(request, "model_dump") else dict(request)
    job = {
        "id": job_id, "drive": name, "engine": "abcde" if media.get("media_type") in {"music", "audiobook"} else "makemkv",
        "state": state, "started_at": now - age, "finished_at": None,
        "return_code": None, "progress": progress,
        "progress_raw": {"current": progress, "total": 100, "max": 100},
        "current_operation": "Copying title 3 of 8", "verification": None,
        "last_message": "Reading and copying the disc", "elapsed_seconds": age,
        "output_dir": f"/mnt/ripping/{media.get('title', 'Demo Disc')}",
        "output_bytes": int(progress * 210_000_000), "replaced_existing": False,
        "health": {"state": "active", "label": "Active", "process_running": True,
                   "seconds_since_progress": 1.2, "seconds_since_activity": 0.4,
                   "seconds_since_output_growth": 0.8, "output_bytes": int(progress * 210_000_000),
                   "warning_count": 0, "last_warning": None},
        "media": media, "sim_last_tick": now,
        "sim_auto_eject": media.get("auto_eject") is not False,
        "sim_verify_until": None,
        "sim_ejected": False,
        "sim_should_fail": state == "ripping" and random.random() < 0.10,
        "sim_fail_at": random.uniform(20.0, 85.0),
    }
    jobs[job_id] = job
    drives[name]["active_job"] = job
    drives[name]["clean_state"] = False
    return job


def reset_state():
    """Restore the six-drive demonstration scene without restarting Manager."""
    drives.clear()
    drives.update({
        "BR1": drive("BR1", "bluray", "disc", "THE_FLASH_SEASON_1_DISC_1"),
        "BR2": drive("BR2", "bluray", "disc", "RUNNING_MAN_2025"),
        "DVD1": drive("DVD1", "dvd", "disc", "PLANET_EARTH_DISC_2"),
        "DVD2": drive("DVD2", "dvd", "empty"),
        "DVD3": drive("DVD3", "dvd", "open"),
        "DVD4": drive("DVD4", "dvd", "disc", "MUSIC_CD"),
    })
    jobs.clear()
    new_job("BR1", RipRequest(title="The Flash", year=2014, season=1, disc=1, media_type="tv"), 37.0, age=510)
    completed = new_job("BR2", RipRequest(title="The Running Man", year=2025, media_type="movie"), 100.0, "complete", 2940)
    completed.update(finished_at=time.time() - 30, return_code=0, current_operation="Complete",
                     last_message="Movie rip completed and output verified",
                     verification={"ok": True, "files_checked": 1}, sim_auto_eject=False)
    failed = new_job("DVD4", RipRequest(title="Demo Album", creator="Demo Artist", media_type="music"), 22.0, "failed", 180)
    failed.update(finished_at=time.time() - 10, return_code=1, current_operation="Rip failed",
                  last_message="Simulated read error",
                  health={"state": "stopped", "label": "Stopped", "process_running": False})


reset_state()


def tick():
    now = time.time()
    for job in jobs.values():
        if job["state"] == "verifying":
            job["elapsed_seconds"] = round(now - job["started_at"], 1)
            if now >= float(job.get("sim_verify_until") or now):
                job.update(state="complete", finished_at=now, return_code=0, progress=100.0,
                           current_operation="Complete",
                           last_message="Rip completed and output verified",
                           verification={"ok": True, "files_checked": 8})
                job["progress_raw"]["current"] = 100.0
                job["health"].update(state="stopped", label="Stopped", process_running=False)
            continue
        if job["state"] == "complete":
            if (job.get("sim_auto_eject") and not job.get("sim_ejected")
                    and job.get("finished_at") and now - job["finished_at"] >= 3):
                d = drives.get(job["drive"])
                if d and (d.get("active_job") or {}).get("id") == job["id"]:
                    d.update(tray="open", active_job=None, clean_state=True)
                    d["media"] = {"present": False, "tray": "open", "label": None,
                                  "reason": "auto_ejected",
                                  "status_message": "Rip complete — disc automatically ejected"}
                job["sim_ejected"] = True
            continue
        if job["state"] != "ripping":
            continue
        elapsed = max(0, now - job.pop("sim_last_tick", now))
        job["sim_last_tick"] = now
        job["progress"] = min(100.0, job["progress"] + elapsed * 1.0)
        job["elapsed_seconds"] = round(now - job["started_at"], 1)
        job["progress_raw"]["current"] = job["progress"]
        job["output_bytes"] = int(job["progress"] * 210_000_000)
        job["health"]["output_bytes"] = job["output_bytes"]
        title = min(8, max(1, int(job["progress"] // 12.5) + 1))
        job["current_operation"] = f"Copying title {title} of 8"
        if job.get("sim_should_fail") and job["progress"] >= job.get("sim_fail_at", 50.0):
            job.update(state="failed", finished_at=now, return_code=1,
                       current_operation="Rip failed",
                       last_message="Simulated disc read error")
            job["health"].update(state="stopped", label="Stopped", process_running=False)
            continue
        if job["progress"] >= 100:
            job.update(state="verifying", progress=99.9, current_operation="Verifying output",
                       last_message="Checking completed files")
            job["progress_raw"]["current"] = 99.9
            job["sim_verify_until"] = now + 3
            job["health"].update(state="verifying", label="Verifying", process_running=False)


def get_drive(name):
    name = name.upper()
    if name not in drives:
        raise HTTPException(404, "Unknown drive")
    return drives[name]


@app.get("/api/info")
def info(): return {"service": "rip-node-api", "node": "demo-node", "version": "0.2.5-simulator", "simulator": True, "docs": "/docs"}

@app.get("/health")
def health(): return {"ok": True, "simulator": True, "time": time.time()}

@app.post("/reset")
def reset():
    reset_state()
    return {"ok": True, "message": "Simulator restored to its starting scene"}

@app.get("/drives")
def list_drives(): tick(); return list(drives.values())

@app.get("/drives/{name}")
def drive_status(name: str): tick(); return get_drive(name)

@app.get("/jobs")
def list_jobs(): tick(); return sorted(jobs.values(), key=lambda j: j["started_at"], reverse=True)

@app.get("/system/stats")
def stats():
    tick(); active = sum(j["state"] == "ripping" for j in jobs.values())
    return {"node": "Demo Rip Node", "hostname": "rip-node-simulator", "uptime_seconds": time.time()-START,
            "os": {"system": "Ubuntu", "release": "26.04 LTS", "machine": "x86_64"},
            "cpu": {"model": "Intel Celeron J4105", "usage_percent": 18.0 + active*12,
                    "per_core_percent": [22, 31, 14, 19], "physical_cores": 4, "logical_cores": 4,
                    "frequency_mhz": {"current": 2400, "min": 800, "max": 2500}, "load_average": [0.42,0.31,0.25]},
            "memory": {"total": 8589934592, "used": 2684354560, "available": 5905580032, "percent": 31.2},
            "swap": {"total": 2147483648, "used": 0, "free": 2147483648, "percent": 0},
            "disks": {"root": {"path":"/", "total":128849018880,"used":32212254720,"free":96636764160,"percent":25},
                      "ripping": {"path":"/mnt/ripping","total":12000000000000,"used":4300000000000,"free":7700000000000,"percent":35.8}},
            "optical_drives": [{"name": n, "device": d["device"], "read_speed_mbps": (11.4 if d["active_job"] and d["active_job"]["state"]=="ripping" else 0), "read_speed_x": (8.4 if d["active_job"] and d["active_job"]["state"]=="ripping" else 0)} for n,d in drives.items()],
            "network": [], "temperatures": {"cpu": 47.0}, "process_count": 126, "top_processes": []}

@app.post("/drives/{name}/rip")
def rip(name: str, request: RipRequest):
    d = get_drive(name)
    if not d["media"]["present"]: raise HTTPException(409, "No disc inserted")
    if d["active_job"] and d["active_job"]["state"] == "ripping": raise HTTPException(409, "Drive is currently ripping")
    return new_job(name.upper(), request)

@app.post("/drives/{name}/eject")
def eject(name: str):
    d=get_drive(name)
    if d["active_job"] and d["active_job"]["state"]=="ripping": raise HTTPException(409,"Drive is currently ripping")
    d.update(tray="open", active_job=None, clean_state=True); d["media"]={"present":False,"tray":"open","label":None,"reason":"tray_open","status_message":"Tray is open"}
    return {"ok":True,"drive":name.upper(),"action":"eject"}

@app.post("/drives/{name}/close")
def close(name: str):
    d = get_drive(name)
    active = d.get("active_job")
    if active and active.get("state") in {"ripping", "verifying"}:
        raise HTTPException(409, "Drive is currently ripping")
    # Closing the tray begins a new disc cycle. Never carry a completed or
    # failed simulated job onto the newly inserted disc.
    d["active_job"] = None
    inserted = random.random() < 0.8
    if inserted:
        media = random_disc("bluray" if d.get("drive_type") == "Blu-ray" else "dvd")
        d.update(tray="disc", clean_state=False)
        d["media"] = media
    else:
        d.update(tray="empty", clean_state=True)
        d["media"] = {"present": False, "tray": "empty", "label": None,
                      "reason": "no_media", "status_message": "No disc inserted"}
    return {"ok": True, "drive": name.upper(), "action": "close", "disc_inserted": inserted}

@app.post("/drives/{name}/cancel")
def cancel(name: str):
    d=get_drive(name); j=d.get("active_job")
    if not j: raise HTTPException(409,"No active rip")
    j.update(state="cancelled",finished_at=time.time(),last_message="Rip cancelled",current_operation="Cancelled")
    return {"ok":True,"drive":name.upper(),"action":"cancel"}

@app.post("/drives/{name}/clear")
def clear(name: str):
    d = get_drive(name)
    d["active_job"] = None
    d["clean_state"] = not bool(d.get("media", {}).get("present"))
    return {"ok": True, "drive": name.upper(), "action": "clear"}

@app.get("/drive-mapping")
def mapping(): return {"mapping": {name: d["device"] for name,d in drives.items()}, "detected": []}
