"""GitHub update status and manual-install controls."""
from __future__ import annotations

import json
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from config import BUNDLED_NODE_FILE, VERSION
import db, nodes as node_client, updates

router=APIRouter(prefix="/updates",tags=["updates"])

class RollbackRequest(BaseModel):
    filename:str=Field(min_length=20,max_length=180)

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
    manager=updates.manager_release(VERSION); node=updates.node_release(); node_versions=[]
    for configured in db.query("SELECT id,name,url,enabled,token FROM nodes ORDER BY id"):
        version=None; error=None; info={}; enabled=bool(configured["enabled"]); simulator=configured["id"]=="simulator" or "/simulator-node" in configured["url"]
        if enabled:
            try:
                info=await node_client.get_json(f"{node_client.base_url(configured)}/api/info",node_client.headers_for(configured)); version=info.get("version")
            except Exception as exc: error=str(exc)
        simulator=bool(simulator or info.get("simulator") or str(version or "").endswith("-simulator"))
        node_versions.append({"id":configured["id"],"name":configured["name"],"version":version,"error":error,"api_node":info.get("node"),"simulator":simulator,"enabled":enabled,"update_available":bool(enabled and not simulator and version and updates.version_tuple(node["version"])>updates.version_tuple(version))})
    return {"source":"GitHub Releases + dedicated updater","automatic_install":False,"manager":{"installed":VERSION,"available":manager},"node_update":node,"nodes":node_versions,"host_updater":updates.read_json("State/github-manager-update.json"),"host_capabilities":updates.host_capabilities(),"rollback_backups":updates.rollback_backups(),"rollback_request":updates.read_json("rollback-manager.request.json") or {"pending":False},"node_updater_status":updates.read_json("State/github-node-update.json"),"node_update_request":updates.read_json("install-nodes.request.json") or {"pending":False}}

@router.post("/install-manager")
def install_manager():
    item=updates.manager_release(VERSION)
    if not item["newer"]: raise HTTPException(status_code=409,detail="No newer Manager release is available")
    updates.write_request("install-manager.request.json",{"requested_at":time.time(),"version":item["version"],"asset_id":item["asset_id"],"filename":item["filename"],"request_id":updates.new_request_id()})
    return {"ok":True,"message":f"Rip Manager v{item['version']} installation queued","update":item}

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
    if not updates.host_capabilities().get("rollback"):raise HTTPException(status_code=409,detail="The dedicated updater is not ready for rollback yet")
    backup=next((item for item in updates.rollback_backups(20) if item["filename"]==req.filename),None)
    if not backup:raise HTTPException(status_code=404,detail="Verified rollback backup was not found")
    updates.write_request("rollback-manager.request.json",{"pending":True,"requested_at":time.time(),"filename":backup["filename"],"version":backup["version"],"request_id":updates.new_request_id()})
    return {"ok":True,"message":f"Rollback to v{backup['version']} queued","backup":backup}

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
