"""GitHub update status and manual-install controls."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from config import BUNDLED_NODE_FILE, VERSION
import db, nodes as node_client, updates

router=APIRouter(prefix="/updates",tags=["updates"])
SHARED_UPDATER_URL=os.getenv("RIP_MANAGER_SHARED_UPDATER_URL","http://host.docker.internal:8093/apps/rip-manager").rstrip("/")

class RollbackRequest(BaseModel):
    filename:str=Field(min_length=20,max_length=180)


def shared_updater_status():
    try:
        req=urllib.request.Request(SHARED_UPDATER_URL+"/status",headers={"User-Agent":"rip-manager"})
        with urllib.request.urlopen(req,timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"current":VERSION,"latest":"","update_available":False,"running":False,"last_result":"","last_log":"","error":f"Shared updater unavailable: {exc}"}


def shared_updater_install():
    req=urllib.request.Request(SHARED_UPDATER_URL+"/install",method="POST",headers={"User-Agent":"rip-manager"})
    try:
        with urllib.request.urlopen(req,timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail=json.loads(exc.read().decode("utf-8")).get("message") or str(exc)
        except Exception:
            detail=str(exc)
        raise HTTPException(status_code=exc.code,detail=detail)
    except Exception as exc:
        raise HTTPException(status_code=503,detail=f"Shared updater unavailable: {exc}")


@router.get("/latest-node-version")
def latest_node_version():
    item=updates.node_release(); return {"version":item["version"],"filename":item["filename"]}

@router.get("/files/latest-node",include_in_schema=False)
def latest_node_file():
    item=updates.node_release()
    if not BUNDLED_NODE_FILE.is_file(): raise HTTPException(status_code=503,detail="Bundled Rip Node file is missing")
    return FileResponse(BUNDLED_NODE_FILE,media_type="text/x-python",filename=item["filename"],headers={"Cache-Control":"no-store"})

@router.get("/status")
async def updates_status():
    shared=shared_updater_status(); manager=updates.manager_release(VERSION); node=updates.node_release(); node_versions=[]
    if shared.get("latest"):
        manager={**manager,"version":shared.get("latest"),"newer":bool(shared.get("update_available"))}
    for configured in db.query("SELECT id,name,url,enabled,token FROM nodes ORDER BY id"):
        version=None; error=None; info={}; enabled=bool(configured["enabled"]); simulator=configured["id"]=="simulator" or "/simulator-node" in configured["url"]
        if enabled:
            try:
                info=await node_client.get_json(f"{node_client.base_url(configured)}/api/info",node_client.headers_for(configured)); version=info.get("version")
            except Exception as exc: error=str(exc)
        simulator=bool(simulator or info.get("simulator") or str(version or "").endswith("-simulator"))
        node_versions.append({"id":configured["id"],"name":configured["name"],"version":version,"error":error,"api_node":info.get("node"),"simulator":simulator,"enabled":enabled,"update_available":bool(enabled and not simulator and version and updates.version_tuple(node["version"])>updates.version_tuple(version))})
    return {"source":"GitHub + AD53 Shared Updater","automatic_install":False,"manager":{"installed":VERSION,"available":manager},"shared_updater":shared,"node_update":node,"nodes":node_versions,"host_updater":shared,"host_capabilities":{"install":not bool(shared.get("error")),"rollback":False,"shared_updater":True},"rollback_backups":[],"rollback_request":{"pending":False},"node_updater_status":updates.read_json("State/github-node-update.json"),"node_update_request":updates.read_json("install-nodes.request.json") or {"pending":False}}

@router.post("/install-manager")
def install_manager():
    status=shared_updater_status()
    if status.get("error"):
        raise HTTPException(status_code=503,detail=status["error"])
    if not status.get("update_available"):
        raise HTTPException(status_code=409,detail="No newer Manager release is available")
    result=shared_updater_install()
    return {"ok":True,"message":result.get("message") or f"Rip Manager v{status.get('latest')} installation queued","update":{"version":status.get("latest"),"newer":True}}

@router.get("/node-install-request")
def node_install_request(): return updates.read_json("install-nodes.request.json") or {"pending":False}

@router.post("/push-nodes")
def install_nodes():
    item=updates.node_release(); request_id=updates.new_request_id()
    updates.write_request("install-nodes.request.json",{"pending":True,"requested_at":time.time(),"version":item["version"],"filename":item["filename"],"source":"bundled-manager","request_id":request_id})
    return {"ok":True,"message":f"Rip Node v{item['version']} installation queued","request_id":request_id}

@router.delete("/node-install-request")
def cancel_node_install():
    updates.clear_request("install-nodes.request.json")
    return {"ok":True,"message":"Queued node update cancelled"}

@router.post("/rollback")
def request_rollback(req:RollbackRequest):
    raise HTTPException(status_code=409,detail="Manager rollback is now handled automatically by the AD53 Shared Updater if an update fails validation")

@router.post("/node-installed")
async def node_installed(request: Request):
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    updates.write_request("State/github-node-update.json",{"state":"success","version":payload.get("version"),"node":payload.get("node"),"request_id":payload.get("request_id"),"installed_at":time.time()})
    updates.clear_request("install-nodes.request.json")
    return {"ok":True}
