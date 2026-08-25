"""Drive mapping proxy endpoints."""
from __future__ import annotations

import re
from fastapi import APIRouter, Body, HTTPException, Query
import httpx

import nodes as node_client
import poller

router = APIRouter(prefix="/nodes/{node_id}/drive-mapping", tags=["drive-mapping"])


async def _request(node_id: str, method: str, path: str = "", body: dict | None = None, params: dict | None = None):
    node = node_client.get_node(node_id)
    try:
        response = await node_client.client().request(
            method,
            f"{node_client.base_url(node)}/drive-mapping{path}",
            headers=node_client.headers_for(node),
            json=body,
            params=params,
            timeout=node_client.COMMAND_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"{node['name']} did not respond in time") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach {node['name']}: {exc}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=payload.get("detail") or str(payload))
    poller.wakeup.set()
    return payload


@router.get("")
async def get_mapping(node_id: str, refresh: bool = Query(default=False)):
    return await _request(node_id, "GET", params={"refresh": str(refresh).lower()})


@router.put("/{drive_name}")
async def set_mapping(node_id: str, drive_name: str, payload: dict = Body(...)):
    return await _request(node_id, "PUT", f"/{drive_name}", payload)


@router.delete("/{drive_name}")
async def delete_mapping(node_id: str, drive_name: str):
    return await _request(node_id, "DELETE", f"/{drive_name}")


def _sr_sort_key(item: dict) -> tuple[int, str]:
    device = str(item.get("device") or "")
    match = re.search(r"/dev/sr(\d+)$", device)
    return (int(match.group(1)) if match else 9999, device)


@router.post("/reconcile")
async def reconcile_mapping(node_id: str):
    before = await _request(node_id, "GET", params={"refresh": "true"})
    mappings = list(before.get("mappings") or [])
    available = sorted(
        [item for item in (before.get("available") or []) if item.get("exists")],
        key=_sr_sort_key,
    )
    if any(item.get("active") for item in mappings):
        raise HTTPException(status_code=409, detail="Drive mappings cannot be rebuilt while a drive is ripping")
    if not available:
        raise HTTPException(status_code=409, detail="No optical drives are currently detected by this Rip Node")

    detected_devices = {str(item.get("device")) for item in available}
    rollback = [
        (str(item.get("name")), str(item.get("device")))
        for item in mappings
        if item.get("name") and item.get("device") in detected_devices
    ]
    try:
        for item in mappings:
            name = str(item.get("name") or "").strip()
            if name:
                await _request(node_id, "DELETE", f"/{name}")
        for index, item in enumerate(available, start=1):
            await _request(node_id, "PUT", f"/DVD{index}", {"device": item["device"], "swap": False})
    except HTTPException:
        try:
            partial = await _request(node_id, "GET")
            for item in partial.get("mappings") or []:
                name = str(item.get("name") or "").strip()
                if name and not item.get("active"):
                    try:
                        await _request(node_id, "DELETE", f"/{name}")
                    except HTTPException:
                        pass
            for name, device in rollback:
                try:
                    await _request(node_id, "PUT", f"/{name}", {"device": device, "swap": False})
                except HTTPException:
                    pass
        finally:
            raise

    result = await _request(node_id, "GET", params={"refresh": "true"})
    return {
        "ok": True,
        "message": f"Rebuilt {len(available)} unique DVD mapping(s)",
        "count": len(available),
        **result,
    }
