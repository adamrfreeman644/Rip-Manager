"""PIN lock endpoints and non-secret security status."""

from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException, Request, Response

import auth
from config import SESSION_COOKIE
import db
from models import LoginRequest

router = APIRouter(tags=["auth"])


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def node_auth_status() -> dict:
    rows = db.query("SELECT id,token FROM nodes ORDER BY id")
    configured = sum(1 for row in rows if row["token"])
    if not rows:
        state = "not_configured"
    elif configured == len(rows):
        state = "authenticated"
    elif configured:
        state = "partial"
    else:
        state = "unauthenticated"
    return {"state": state, "configured_nodes": len(rows), "authenticated_nodes": configured}


@router.get("/auth/status")
def auth_status(request: Request):
    enabled = db.get_setting_bool("lock_enabled")
    return {
        "controller_protection": "enabled" if enabled else "disabled",
        "lock_enabled": enabled,
        "pin_set": auth.pin_is_set(),
        "authenticated": not enabled or auth.session_valid(request.cookies.get(SESSION_COOKIE)),
        "node_api_authentication": node_auth_status(),
    }


@router.post("/auth/login")
def login(req: LoginRequest, request: Request, response: Response):
    if not db.get_setting_bool("lock_enabled"):
        return {"ok": True}

    client = client_key(request)
    wait = auth.throttle_remaining(client)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many incorrect PINs. Try again in {math.ceil(wait)} seconds",
        )

    if not auth.verify_pin(req.pin, db.get_setting("pin_hash")):
        auth.record_failure(client)
        raise HTTPException(status_code=401, detail="Incorrect PIN")

    auth.clear_failures(client)
    auth.issue_session(response)
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    auth.revoke_session(request.cookies.get(SESSION_COOKIE), response)
    return {"ok": True}
