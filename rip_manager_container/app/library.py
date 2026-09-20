"""Fast ownership catalogue backed by SQLite and recoverable disc-info.json manifests."""

from __future__ import annotations
import hashlib, json, re, time
from pathlib import Path
import db

def _clean_barcode(value):
    value = re.sub(r"\D", "", str(value or ""))
    return value or None

def _has_extras(payload: dict, folder: Path) -> bool:
    images = payload.get("images") or {}
    if images.get("extras"): return True
    for item in payload.get("files") or []:
        p = str(item.get("path") or "").lower()
        if p.startswith("extras/") or "/extras/" in p: return True
    return (folder / "extras").is_dir() and any((folder / "extras").iterdir())

def _upsert(manager_job_id=None, barcode=None, title=None, year=None, media_type=None,
            fmt=None, final_dir=None, has_extras=False, source="manual"):
    now=time.time(); barcode=_clean_barcode(barcode)
    with db.write() as conn:
        row = conn.execute("SELECT id FROM library_items WHERE manager_job_id=?",
                           (manager_job_id,)).fetchone() if manager_job_id else None
        if row:
            conn.execute("""UPDATE library_items SET barcode=?,title=?,year=?,media_type=?,format=?,
                         final_dir=?,has_extras=?,source=?,updated_at=? WHERE id=?""",
                         (barcode,title,year,media_type,fmt,final_dir,int(has_extras),source,now,row["id"]))
            return row["id"], False
        cur=conn.execute("""INSERT INTO library_items(manager_job_id,barcode,title,year,media_type,
                         format,final_dir,has_extras,source,created_at,updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (manager_job_id,barcode,title,year,media_type,fmt,final_dir,int(has_extras),source,now,now))
        return cur.lastrowid, True

def search(query: str="") -> list[dict]:
    q=(query or "").strip()
    if not q:
        rows=db.query("SELECT * FROM library_items ORDER BY COALESCE(title,barcode) COLLATE NOCASE LIMIT 250")
    elif q.isdigit():
        rows=db.query("SELECT * FROM library_items WHERE barcode=? ORDER BY title COLLATE NOCASE",(q,))
    else:
        rows=db.query("SELECT * FROM library_items WHERE title LIKE ? COLLATE NOCASE ORDER BY title COLLATE NOCASE LIMIT 100",(f"%{q}%",))
    return [dict(r)|{"has_extras":bool(r["has_extras"])} for r in rows]

def bulk_add(text: str) -> dict:
    submitted=[_clean_barcode(x) for x in re.split(r"[\s,;]+",text or "") if x.strip()]
    valid=[x for x in submitted if x and 6 <= len(x) <= 18]
    invalid=len(submitted)-len(valid); added=existing=duplicates=0; seen=set()
    for code in valid:
        if code in seen: duplicates+=1; continue
        seen.add(code)
        if db.query_one("SELECT 1 FROM library_items WHERE barcode=?",(code,)): existing+=1; continue
        _upsert(barcode=code,source="bulk_upc"); added+=1
    return {"submitted":len(submitted),"added":added,"already_owned":existing,"duplicates":duplicates,"invalid":invalid}

def check_manifests() -> dict:
    root=Path(db.get_setting("mover_destination_root","/media")).resolve()
    folders=db.get_setting_json("mover_destination_folders",{})
    found=updated=skipped=0
    for relative in folders.values():
        base=(root/relative).resolve()
        try: base.relative_to(root)
        except ValueError: continue
        if not base.is_dir(): continue
        for manifest in base.rglob("disc-info.json"):
            found+=1; folder=manifest.parent
            try:
                payload=json.loads(manifest.read_text(encoding="utf-8"))
                if not isinstance(payload,dict): raise ValueError()
            except (OSError,ValueError,TypeError):
                skipped+=1; continue
            mid=payload.get("manager_job_id")
            barcode=payload.get("upc") or (payload.get("physical_media") or {}).get("upc")
            fmt=(payload.get("physical_media") or {}).get("format") or payload.get("format")
            _upsert(mid,barcode,payload.get("title") or folder.name,payload.get("year"),
                    payload.get("media_type"),fmt,str(folder),_has_extras(payload,folder),"manifest")
            updated+=1
    return {"manifests_found":found,"database_updated":updated,"skipped":skipped}
