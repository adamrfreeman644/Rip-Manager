"""Node installation/adoption endpoints."""
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

import adoption
import nodes as node_client

router = APIRouter(prefix="/adoption", tags=["adoption"])
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


class SSHTestRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=500)
    sudo_password: Optional[str] = Field(default=None, max_length=500)


class AdoptRequest(SSHTestRequest):
    node_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    api_port: int = Field(default=8000, ge=1, le=65535)
    node_url: Optional[str] = Field(default=None, max_length=300)
    manager_url_for_node: str = Field(min_length=4, max_length=300)
    output_path: str = Field(default="/mnt/ripping", min_length=1, max_length=500)
    nas_share: Optional[str] = Field(default=None, max_length=500)
    nas_username: Optional[str] = Field(default=None, max_length=200)
    nas_password: Optional[str] = Field(default=None, max_length=500)


class ExistingNodeTestRequest(BaseModel):
    url: str = Field(min_length=4, max_length=300)
    token: Optional[str] = Field(default=None, max_length=500)


@router.post("/test-ssh")
def test_ssh(req: SSHTestRequest):
    return adoption.test_ssh(req.host, req.ssh_port, req.username, req.password, req.sudo_password)


@router.post("/install")
def install(req: AdoptRequest):
    return adoption.install_and_adopt(**req.model_dump())


def _run_install(job_id: str, payload: dict) -> None:
    def report(stage: str, percent: int, message: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            job.update(stage=stage, percent=percent, message=message, updated_at=time.time())
            job["events"].append({"stage": stage, "percent": percent, "message": message, "at": time.time()})

    try:
        result = adoption.install_and_adopt(**payload, progress=report)
        with _jobs_lock:
            _jobs[job_id].update(state="complete", result=result, percent=100, updated_at=time.time())
    except HTTPException as exc:
        with _jobs_lock:
            _jobs[job_id].update(state="failed", error=str(exc.detail), updated_at=time.time())
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id].update(state="failed", error=f"Unexpected installer error: {type(exc).__name__}: {exc}", updated_at=time.time())
    finally:
        payload.clear()


@router.post("/install/start", status_code=202)
def start_install(req: AdoptRequest):
    job_id = uuid.uuid4().hex
    now = time.time()
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id, "state": "running", "stage": "queued", "percent": 0,
            "message": "Installer queued", "error": None, "result": None,
            "started_at": now, "updated_at": now,
            "events": [{"stage": "queued", "percent": 0, "message": "Installer queued", "at": now}],
        }
    payload = req.model_dump()
    threading.Thread(target=_run_install, args=(job_id, payload), daemon=True, name=f"adopt-{job_id[:8]}").start()
    return {"job_id": job_id}


@router.get("/install/{job_id}")
def install_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Installer job not found; it may have been cleared by a Manager restart")
        return dict(job)


@router.post("/test-existing")
async def test_existing(req: ExistingNodeTestRequest):
    headers={"Authorization":f"Bearer {req.token}"} if req.token else {}
    try: info=await node_client.get_json(f"{req.url.rstrip('/')}/api/info",headers)
    except Exception as exc: raise HTTPException(status_code=502,detail=f"Could not reach Node API: {exc}") from exc
    if info.get("service")!="rip-node-api": raise HTTPException(status_code=422,detail="Address responded, but it is not a Rip Node API")
    return {"ok":True,"node":info.get("node"),"version":info.get("version")}
