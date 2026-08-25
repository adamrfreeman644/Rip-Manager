"""Authenticated configuration proxy for settings owned by a real Rip Node."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
import httpx

import nodes as node_client

router = APIRouter(prefix="/nodes/{node_id}/storage", tags=["node-settings"])


async def _request(node_id: str, method: str, body: dict | None = None):
    node = node_client.get_node(node_id)
    try:
        response = await node_client.client().request(
            method,
            f"{node_client.base_url(node)}/settings/storage",
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
    return payload


@router.get("")
async def get_storage(node_id: str):
    return await _request(node_id, "GET")


@router.put("")
async def set_storage(node_id: str, payload: dict = Body(...)):
    return await _request(node_id, "PUT", payload)
