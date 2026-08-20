"""Talking to Rip Nodes.

One HTTP client is shared by the whole application. Version 0.7.1 built a new
``httpx.AsyncClient`` for every node on every poll, which with a two-second
active cadence meant a fresh TCP connection and TLS-less handshake per drive
per poll for the life of the container. A single pooled client reuses
connections and makes the poll loop measurably cheaper.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from config import (
    COMMAND_CONNECT_TIMEOUT,
    COMMAND_READ_TIMEOUT,
    REQUEST_TIMEOUT,
)
import db

_client: Optional[httpx.AsyncClient] = None

POLL_TIMEOUT = httpx.Timeout(REQUEST_TIMEOUT, connect=min(REQUEST_TIMEOUT, 5.0))
COMMAND_TIMEOUT = httpx.Timeout(COMMAND_READ_TIMEOUT, connect=COMMAND_CONNECT_TIMEOUT)


async def start_client() -> None:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            timeout=POLL_TIMEOUT,
            follow_redirects=False,
            # Nodes are on the local network. Honouring HTTP_PROXY or ALL_PROXY
            # from the environment would send every poll through a proxy that
            # cannot reach them, so environment proxies are ignored.
            trust_env=False,
        )


async def stop_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client used before startup")
    return _client


def headers_for(node: sqlite3.Row) -> Dict[str, str]:
    return {"Authorization": f"Bearer {node['token']}"} if node["token"] else {}


def base_url(node: sqlite3.Row) -> str:
    return node["url"].rstrip("/")


async def get_json(url: str, headers: Dict[str, str], timeout: httpx.Timeout = POLL_TIMEOUT) -> Any:
    response = await client().get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_node(node_id: str) -> sqlite3.Row:
    node = db.query_one("SELECT * FROM nodes WHERE id=?", (node_id,))
    if not node:
        raise HTTPException(status_code=404, detail=f"Unknown node {node_id}")
    if not node["enabled"]:
        raise HTTPException(status_code=409, detail=f"Node {node_id} is disabled")
    return node


def enabled_nodes() -> List[sqlite3.Row]:
    return db.query("SELECT * FROM nodes WHERE enabled=1 ORDER BY id")


def cached_drives(node_id: str) -> List[dict]:
    row = db.query_one("SELECT drives_json FROM node_cache WHERE node_id=?", (node_id,))
    if not row:
        return []
    try:
        return json.loads(row["drives_json"] or "[]")
    except json.JSONDecodeError:
        return []


def find_drive(name: str) -> Tuple[sqlite3.Row, dict]:
    """Locate a drive by bare name across every enabled node."""
    target = name.upper()
    matches = []
    for node in enabled_nodes():
        for drive in cached_drives(node["id"]):
            if str(drive.get("name", "")).upper() == target:
                matches.append((node, drive))
    if not matches:
        raise HTTPException(status_code=404, detail=f"Drive {target} not found")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"Drive {target} exists on multiple nodes")
    return matches[0]


def find_cached_drive(node_id: str, drive_name: str) -> Optional[dict]:
    target = drive_name.upper()
    for drive in cached_drives(node_id):
        if str(drive.get("name", "")).upper() == target:
            return drive
    return None


async def post(node: sqlite3.Row, path: str, body: Optional[dict] = None) -> Any:
    """Forward a command to a node and translate its failure into ours."""
    try:
        response = await client().post(
            f"{base_url(node)}{path}",
            headers=headers_for(node),
            json=body,
            timeout=COMMAND_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504, detail=f"{node['name']} did not respond in time"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach {node['name']}: {exc}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text}

    if response.status_code >= 400:
        message = payload.get("detail") or payload.get("text") if isinstance(payload, dict) else payload
        raise HTTPException(status_code=response.status_code, detail=f"{node['name']} returned: {message}")
    return payload


def drive_preference(node_id: str, drive_name: str, key: str, default: bool = True) -> bool:
    """Read a per-drive toggle set in Settings → Drives."""
    preferences = db.get_setting_json("drive_preferences", {})
    entry = preferences.get(f"{node_id}:{drive_name.upper()}") or {}
    value = entry.get(key)
    return default if value is None else bool(value)


async def gather_settled(*awaitables: Any) -> list:
    return await asyncio.gather(*awaitables, return_exceptions=True)
