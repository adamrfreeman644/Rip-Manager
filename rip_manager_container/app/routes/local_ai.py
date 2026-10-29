"""Local AI controls use the Manager's existing controller authentication."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import httpx
import archive, db, local_ai

router = APIRouter(prefix="/api/local-ai", tags=["local AI organiser"])

class Config(BaseModel):
    name: str = Field(default="AI Node 1 — MacBook M1", min_length=1, max_length=100)
    base_url: str = Field(max_length=200)
    model: str = Field(min_length=1, max_length=150)
    weekly: bool = False
    auto_apply: bool = False

class Propose(BaseModel):
    job_id: str = Field(min_length=1, max_length=200)

def call(fn, *args):
    try: return fn(*args)
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        raise HTTPException(422, str(exc)[:500]) from exc

@router.get("")
def status():
    return {"config": local_ai.config(), "plans": local_ai.plans(),
            "last_run": db.get_setting_json("local_ai_last_run", {}),
            "media": [{"job_id": r["manager_job_id"], "title": r.get("display_title") or r.get("title")}
                      for r in archive.list_media() if r.get("final_dir") and r.get("state") == "complete" and r.get("media_type") in {"movie", "tv"}]}

@router.put("/config")
def configure(req: Config): return call(local_ai.save_config, req.model_dump())

@router.post("/test")
def test(): return call(local_ai.test_connection)

@router.post("/plans")
def propose(req: Propose): return call(local_ai.propose, req.job_id)

@router.post("/plans/{plan_id}/apply")
def apply(plan_id: str): return call(local_ai.apply, plan_id)

@router.post("/plans/{plan_id}/undo")
def undo(plan_id: str): return call(local_ai.undo, plan_id)
