"""Drive mapping proxy endpoints.

The Node owns physical hardware mapping. Rip Manager exposes it in the Web UI
without storing a second conflicting copy of the mapping.
"""
from __future__ import annotations

import re
from fastapi import APIRouter, Body, HTTPException
import httpx

import nodes as node_client
import poller

router = APIRouter(prefix="/nodes/{node_id}/drive-mapping", tags=["drive-mapping"])


async def _request(node_id: str, method: str, path: str = "", body: dict | None = None):
    node = node_client.get_node(node_id)
    try:
        response = await node_client.client().request(
            method,
            f"{node_client.base_url(node)}/drive-mapping{path}",
            headers=node_client.headers_for(node),
            json=body,
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
async def get_mapping(node_id: str):
    return await _request(node_id, "GET")


@router.put("/{drive_name}")
async def set_mapping(node_id: str, drive_name: str, payload: dict = Body(...)):
    return await _request(node_id, "PUT", f"/{drive_name}", payload)


@router.delete("/{drive_name}")
async def delete_mapping(node_id: str, drive_name: str):
    return await _request(node_id, "DELETE", f"/{drive_name}")


def _sr_sort_key(item: dict) -> tuple[int, str]:
    device = str(item.get("device") or "")
    match = re.search(r"/dev/sr(\\d+)$", device)
    return (int(match.group(1)) if match else 9999, device)


@router.post("/reconcile")
async def reconcile_mapping(node_id: str):
    """Rebuild this node's logical DVD slots from detected optical hardware.

    The result is intentionally exact:
      N detected optical drives -> DVD1 ... DVDN
    Existing surplus/missing/custom mappings are removed. Every detected
    physical drive is assigned once. Active ripping prevents the operation.
    """
    before = await _request(node_id, "GET")
    mappings = list(before.get("mappings") or [])
    available = sorted(
        [item for item in (before.get("available") or []) if item.get("exists")],
        key=_sr_sort_key,
    )

    if any(item.get("active") for item in mappings):
        raise HTTPException(
            status_code=409,
            detail="Drive mappings cannot be rebuilt while a drive is ripping",
        )
    if not available:
        raise HTTPException(
            status_code=409,
            detail="No optical drives are currently detected by this Rip Node",
        )

    # Snapshot valid current assignments for best-effort rollback if a network
    # interruption occurs during the rebuild.
    detected_devices = {str(item.get("device")) for item in available}
    rollback = [
        (str(item.get("name")), str(item.get("device")))
        for item in mappings
        if item.get("name") and item.get("device") in detected_devices
    ]

    created: list[str] = []
    try:
        # Clear old logical slots first. This removes stale DVD4/DVD5 entries
        # automatically when fewer physical drives are now present.
        for item in mappings:
            name = str(item.get("name") or "").strip()
            if name:
                await _request(node_id, "DELETE", f"/{name}")

        # Exact one-to-one rebuild. Sorting by /dev/srX makes the result easy to
        # understand, while Node API v0.2.4 saves each drive's persistent ID_PATH.
        for index, item in enumerate(available, start=1):
            name = f"DVD{index}"
            await _request(
                node_id, "PUT", f"/{name}",
                {"device": item["device"], "swap": False},
            )
            created.append(name)

    except HTTPException:
        # Best effort: clear the partial rebuild and restore previously valid
        # assignments. Never mask the original failure with rollback failures.
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
                    await _request(
                        node_id, "PUT", f"/{name}",
                        {"device": device, "swap": False},
                    )
                except HTTPException:
                    pass
        finally:
            raise

    result = await _request(node_id, "GET")
    return {
        "ok": True,
        "message": f"Rebuilt {len(available)} unique DVD mapping(s)",
        "count": len(available),
        **result,
    }
