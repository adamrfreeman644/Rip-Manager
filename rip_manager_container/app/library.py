"""Tiny searchable index over authoritative disc-info.json manifests."""

from __future__ import annotations
import json, re, time
from pathlib import Path
import db

def _clean_barcode(value):
    value=re.sub(r"\D","",str(value or ""))
    return value or None

def _manifest_upc(payload):
    return _clean_barcode(payload.get("upc") or (payload.get("physical_media") or {}).get("upc"))

def _read_manifest(path: Path):
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload,dict) else None
    except (OSError,ValueError,TypeError):
        return None

def _index_manifest(path: Path,payload: dict):
    now=time.time(); title=payload.get("title") or path.parent.name; barcode=_manifest_upc(payload)
    with db.write() as conn:
        row=conn.execute("SELECT id FROM library_items WHERE manifest_path=?",(str(path),)).fetchone()
        if row:
            conn.execute("UPDATE library_items SET title=?,barcode=?,updated_at=? WHERE id=?",(title,barcode,now,row["id"]))
            item_id=row["id"]
        else:
            cur=conn.execute("INSERT INTO library_items(title,barcode,manifest_path,created_at,updated_at) VALUES (?,?,?,?,?)",(title,barcode,str(path),now,now))
            item_id=cur.lastrowid
        if barcode:
            conn.execute("DELETE FROM owned_upcs WHERE barcode=?",(barcode,))
    return item_id

def search(query: str="", **_ignored) -> list[dict]:
    q=(query or "").strip()
    if q.isdigit():
        rows=db.query("SELECT id,title,barcode,manifest_path FROM library_items WHERE barcode=? ORDER BY title COLLATE NOCASE",(q,))
        if rows: return [dict(r) for r in rows]
        owned=db.query("SELECT id,barcode FROM owned_upcs WHERE barcode=?",(q,))
        return [{"id":f"upc:{r['id']}","title":"Owned — metadata pending","barcode":r["barcode"],"manifest_path":None} for r in owned]
    if q:
        rows=db.query("SELECT id,title,barcode,manifest_path FROM library_items WHERE title LIKE ? COLLATE NOCASE ORDER BY title COLLATE NOCASE LIMIT 1000",(f"%{q}%",))
    else:
        rows=db.query("SELECT id,title,barcode,manifest_path FROM library_items ORDER BY title COLLATE NOCASE LIMIT 1000")
    return [dict(r) for r in rows]

def bulk_add(text: str) -> dict:
    submitted=[_clean_barcode(x) for x in re.split(r"[\s,;]+",text or "") if x.strip()]
    valid=[x for x in submitted if x and 6<=len(x)<=18]
    invalid=len(submitted)-len(valid); added=existing=duplicates=0; seen=set()
    for code in valid:
        if code in seen: duplicates+=1; continue
        seen.add(code)
        if db.query_one("SELECT 1 FROM library_items WHERE barcode=?",(code,)) or db.query_one("SELECT 1 FROM owned_upcs WHERE barcode=?",(code,)):
            existing+=1; continue
        with db.write() as conn: conn.execute("INSERT INTO owned_upcs(barcode,created_at) VALUES (?,?)",(code,time.time()))
        added+=1
    return {"submitted":len(submitted),"added":added,"already_owned":existing,"duplicates":duplicates,"invalid":invalid}

def check_manifests() -> dict:
    root=Path(db.get_setting("mover_destination_root","/media")).resolve()
    folders=db.get_setting_json("mover_destination_folders",{})
    found=updated=skipped=0; seen=set()
    for relative in folders.values():
        base=(root/relative).resolve()
        try: base.relative_to(root)
        except ValueError: continue
        if not base.is_dir(): continue
        for manifest in base.rglob("disc-info.json"):
            found+=1; payload=_read_manifest(manifest)
            if payload is None: skipped+=1; continue
            _index_manifest(manifest,payload); seen.add(str(manifest)); updated+=1
    # The index is disposable: remove entries whose manifests no longer exist in configured media.
    with db.write() as conn:
        rows=conn.execute("SELECT id,manifest_path FROM library_items").fetchall()
        for row in rows:
            if row["manifest_path"] not in seen:
                conn.execute("DELETE FROM library_items WHERE id=?",(row["id"],))
    return {"manifests_found":found,"database_updated":updated,"skipped":skipped}

def get_item(item_id):
    if isinstance(item_id,str) and item_id.startswith("upc:"):
        try: row=db.query_one("SELECT barcode FROM owned_upcs WHERE id=?",(int(item_id.split(":",1)[1]),))
        except ValueError: row=None
        return {"title":"Owned — metadata pending","barcode":row["barcode"],"manifest":None} if row else None
    try: row=db.query_one("SELECT id,title,barcode,manifest_path FROM library_items WHERE id=?",(int(item_id),))
    except (TypeError,ValueError): return None
    if not row: return None
    item=dict(row); item["manifest"]=_read_manifest(Path(item["manifest_path"])) if item.get("manifest_path") else None
    return item
