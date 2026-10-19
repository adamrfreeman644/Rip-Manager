"""Shared, progress-aware catalog maintenance with one background worker."""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import archive, db, library, media_intake, recovery

_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="library-maintenance")
_lock=threading.RLock()
_future=None


def health(progress=None):
    manifests=archive.backfill_missing_manifests(preview=True,progress=progress)
    intake=media_intake.scan()
    stranded=recovery.discover()
    eligible=0
    for item in stranded:
        try:
            if recovery.analyse(item["node_id"],item["folder"],item["media_type"])["ready"]:
                eligible+=1
        except (OSError,ValueError):
            pass
    return {"ok":True,"healthy":manifests["manifests_existing"],"missing_json":manifests["manifests_missing"],
            "invalid_json":manifests["manifests_invalid"],"waiting_intake":intake["count"],
            "stranded_rips":len(stranded),"eligible_stranded_rips":eligible,
            "inaccessible_roots":manifests["inaccessible_roots"],
            "safe_repairs":manifests["manifests_missing"]+manifests["manifests_invalid"]+eligible}


def repair(progress=None):
    result=archive.backfill_missing_manifests(preview=False,repair_invalid=True,progress=progress)
    queued=0;attention=[]
    for item in recovery.discover():
        try:
            if item["destination_exists"]:
                raise ValueError("Destination already exists; source retained")
            recovery.adopt(item["node_id"],item["folder"],item["media_type"])
            queued+=1
        except (OSError,ValueError) as exc:
            attention.append({"folder":item["folder"],"error":str(exc)})
    result.update(index=library.check_manifests(progress=progress),stranded_queued=queued,needs_attention=attention)
    return result


def status():
    state=db.get_setting_json("library_maintenance_job",{})
    if state.get("status")=="running" and (_future is None or _future.done()):
        state.update(status="interrupted",error="Manager restarted; run this safe operation again")
    return state


def ensure_initial_index():
    """Start initial indexing once; report failures instead of spinning on GETs."""
    signature=library.index_signature()
    if db.get_setting("library_index_signature")==signature:
        return None
    previous=status()
    if previous.get("kind")=="index" and previous.get("signature")==signature and previous.get("status") in {"failed","interrupted"}:
        return previous
    return start("index")


def start(kind):
    global _future
    if kind not in {"health","repair","index","scan","backfill"}:
        raise ValueError("Unsupported maintenance operation")
    with _lock:
        if _future is not None and not _future.done():
            return status()
        state={"id":uuid.uuid4().hex,"kind":kind,"status":"running","done":0,"total":0,"current":"Starting","started_at":time.time()}
        if kind=="index": state["signature"]=library.index_signature()
        db.set_setting("library_maintenance_job",json.dumps(state))
        _future=_executor.submit(_run,state)
        return dict(state)


def _run(state):
    def progress(done,total,current):
        state.update(done=done,total=total,current=current)
        db.set_setting("library_maintenance_job",json.dumps(state))
    try:
        functions={"health":health,"repair":repair,"index":library.check_manifests,
                   "scan":lambda progress:library.refresh_catalog(progress=progress)["archive"],
                   "backfill":lambda progress:archive.backfill_missing_manifests(preview=False,progress=progress)}
        state["result"]=functions[state["kind"]](progress=progress)
        state.update(status="complete",finished_at=time.time())
    except Exception as exc:
        state.update(status="failed",error=str(exc),finished_at=time.time())
    finally:
        db.set_setting("library_maintenance_job",json.dumps(state))
        conn=getattr(db._local,"conn",None)
        if conn is not None:
            conn.close(); db._local.conn=None
