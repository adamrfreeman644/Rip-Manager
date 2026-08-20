"""Rip Manager — central controller for a fleet of Rip Nodes.

Start with:
    uvicorn main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import auth
from config import CORS_ORIGINS, SESSION_COOKIE, STATIC_DIR, VERSION, ensure_directories
import db
import nodes as node_client
import poller
import simulator_node
from routes import ROUTERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("rip-manager")


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_directories()
    db.init_db()
    db.load_nodes_file()
    db.prune_history()
    await node_client.start_client()
    poller.start()
    log.info("Rip Manager %s ready", VERSION)
    yield
    await poller.stop()
    await node_client.stop_client()


app = FastAPI(
    title="Rip Manager",
    version=VERSION,
    description="Central manager for Rip Node APIs",
    lifespan=lifespan,
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/simulator-node", simulator_node.app, name="simulator-node")

# Node update endpoints stay public because the node updaters run as root
# systemd units with no browser session to present.
PUBLIC_PATHS = {
    "/",
    "/health",
    "/api/info",
    "/auth/status",
    "/auth/login",
    "/updates/latest-node-version",
    "/updates/latest-node-updater-version",
    "/updates/force-node-request",
    "/updates/files/latest-node",
    "/updates/node-install-request",
    "/updates/node-installed",
    "/updates/files/latest-node-updater",
}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    # The built-in simulator is polled over loopback and has no browser
    # session. It cannot access hardware, storage or external computers.
    if path.startswith("/static/") or path.startswith("/simulator-node/") or path in PUBLIC_PATHS:
        return await call_next(request)
    if db.get_setting_bool("lock_enabled") and not auth.session_valid(request.cookies.get(SESSION_COOKIE)):
        return JSONResponse(status_code=401, content={"detail": "Login required"})
    return await call_next(request)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/info", tags=["service"])
def api_info():
    return {"service": "rip-manager", "version": VERSION, "docs": "/docs"}


@app.get("/health", tags=["service"])
def health():
    return {"ok": True, "time": time.time()}


for router in ROUTERS:
    app.include_router(router)
