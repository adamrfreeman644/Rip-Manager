"""Node storage and authenticated SMB-share management."""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException
import httpx
from pydantic import BaseModel, Field
from typing import Optional

import adoption
import nodes as node_client

router = APIRouter(prefix="/nodes/{node_id}", tags=["node-settings"])


class SmbCredentials(BaseModel):
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=500)
    sudo_password: Optional[str] = Field(default=None, max_length=500)


def _ssh_host(node) -> str:
    host = urlparse(node_client.base_url(node)).hostname
    if not host:
        raise HTTPException(status_code=422, detail="The node URL does not contain a usable hostname or IP")
    return host


def _assert_node_idle(node_id: str) -> None:
    active = [
        drive.get("name")
        for drive in node_client.cached_drives(node_id)
        if drive.get("active_job")
    ]
    if active:
        raise HTTPException(
            status_code=409,
            detail="Dependency changes are blocked while these drives are active: " + ", ".join(active),
        )


async def _storage_request(node_id: str, method: str, body: dict | None = None):
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


@router.get("/storage")
async def get_storage(node_id: str):
    return await _storage_request(node_id, "GET")


@router.put("/storage")
async def set_storage(node_id: str, payload: dict = Body(...)):
    return await _storage_request(node_id, "PUT", payload)


@router.post("/smb/setup")
async def setup_smb(node_id: str, credentials: SmbCredentials):
    node = node_client.get_node(node_id)
    storage = await _storage_request(node_id, "GET")
    if not storage.get("exists") or not storage.get("writable"):
        raise HTTPException(status_code=422, detail="Fix the node storage location before creating its SMB share")
    return await asyncio.to_thread(
        adoption.setup_smb,
        _ssh_host(node), credentials.ssh_port, credentials.username,
        credentials.password, credentials.sudo_password, storage["path"],
    )


@router.post("/smb/test")
async def test_smb(node_id: str, credentials: SmbCredentials):
    node = node_client.get_node(node_id)
    return await asyncio.to_thread(
        adoption.test_smb,
        _ssh_host(node), credentials.ssh_port, credentials.username, credentials.password,
    )


@router.post("/dependencies/check")
async def check_dependencies(node_id: str, credentials: SmbCredentials):
    node = node_client.get_node(node_id)
    return await asyncio.to_thread(
        adoption.check_node_dependencies,
        _ssh_host(node), credentials.ssh_port, credentials.username,
        credentials.password, credentials.sudo_password,
    )


@router.post("/dependencies/update")
async def update_dependencies(node_id: str, credentials: SmbCredentials):
    node = node_client.get_node(node_id)
    _assert_node_idle(node_id)
    return await asyncio.to_thread(
        adoption.update_node_dependencies,
        _ssh_host(node), credentials.ssh_port, credentials.username,
        credentials.password, credentials.sudo_password,
    )
