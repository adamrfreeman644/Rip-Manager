"""Tiny searchable index over authoritative disc-info.json manifests."""

from __future__ import annotations
import json, re, time
from difflib import SequenceMatcher
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

def _title_key(value):
    value=re.sub(r"\b(?:the|a|an|dvd|blu ray|bluray|uhd|4k|special edition|collector s edition)\b"," ",str(value or "").lower())
    return " ".join(re.findall(r"[a-z0-9]+",value))

def _media_folder(item):
    import archive
    folder = archive.existing_folder(item) if item.get("manager_job_id") else None
    if folder is None and item.get("manifest_path"):
        folder = Path(item["manifest_path"]).parent
    if folder is None:
        return None
    folder = folder.resolve()
    roots = [db.get_setting("mover_destination_root", "/media"),
             *db.get_setting_json("mover_node_mounts", {}).values()]
    return folder if any(folder.is_relative_to(Path(root).resolve()) for root in roots) else None


def local_metadata(item):
    """Read Jellyfin-compatible sidecars without modifying them or disc photos."""
    from xml.etree import ElementTree
    folder = _media_folder(item)
    if folder is None:
        return {}
    result = {}
    names = ["movie.nfo", "tvshow.nfo", "season.nfo", "album.nfo", "artist.nfo"]
    try:
        files = list(folder.iterdir())
    except OSError:
        return {}
    nfos = [folder / name for name in names if (folder / name).is_file()]
    if not nfos:
        nfos = [path for path in files if path.suffix.lower() == ".nfo"]
        if len(nfos) != 1:
            nfos = []
    if nfos:
        try:
            path = nfos[0].resolve()
            if path.parent == folder and path.stat().st_size <= 1024 * 1024:
                raw = path.read_bytes()
                if b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper():
                    node = ElementTree.fromstring(raw)
                    result = {key: node.findtext(tag) for key, tag in
                              (("title", "title"), ("year", "year"), ("overview", "plot"),
                               ("rating", "rating"), ("runtime", "runtime")) if node.findtext(tag)}
                    result["genres"] = [tag.text for tag in node.findall("genre") if tag.text]
                    result["source"] = "Local NFO"
        except (OSError, ValueError, ElementTree.ParseError):
            pass
    return result


def artwork_path(item):
    folder = _media_folder(item)
    if folder is None:
        return None
    for stem in ("poster", "folder", "cover"):
        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            path = (folder / (stem + suffix)).resolve()
            if path.parent == folder and path.is_file():
                return path
    return None


def _enrich(item):
    item["local_metadata"] = local_metadata(item)
    if artwork_path(item):
        from urllib.parse import quote
        item["artwork_url"] = "/api/collection/" + quote(str(item["id"]), safe="") + "/artwork"
    return item


def _collection() -> list[dict]:
    """Join manifests and rip history by folder/record identity, never title alone."""
    import archive
    if not db.query_one("SELECT 1 FROM library_items LIMIT 1"):
        check_manifests()
    folders = db.get_setting_json("mover_destination_folders", {})
    destination = Path(db.get_setting("mover_destination_root", "/media")).resolve()

    def is_supplemental_folder(folder):
        if not folder:
            return False
        try:
            candidate = Path(folder).resolve()
        except OSError:
            return False
        for relative in folders.values():
            base = (destination / relative).resolve()
            try:
                candidate.relative_to(base)
            except ValueError:
                continue
            return archive.content_folder(candidate, base) != candidate
        return False

    # Existing scan rows made before supplemental-folder handling can remain in
    # the database. Hide them here too, so the Library is immediately a clean
    # view of Jellyfin titles plus discs/jobs awaiting a rip.
    physical = [item for item in archive.list_media()
                if not is_supplemental_folder(item.get("existing_dir") or item.get("final_dir"))]
    by_folder = {}
    by_job = {}
    for item in physical:
        by_job[item["manager_job_id"]] = item
        by_job[item.get("latest_attempt_id", item["manager_job_id"])] = item
        for key in ("existing_dir", "final_dir", "expected_dir"):
            if item.get(key):
                by_folder[str(Path(item[key]).resolve())] = item
    result = []
    used = set()
    for row in db.query("SELECT id,title,barcode,manifest_path FROM library_items"):
        item = dict(row)
        path = Path(item["manifest_path"])
        # Do not surface old manifests left inside recognised bonus folders.
        if is_supplemental_folder(path.parent):
            continue
        manifest = _read_manifest(path) or {}
        physical_item = by_folder.get(str(path.parent.resolve())) or by_job.get(manifest.get("manager_job_id"))
        if physical_item:
            used.add(physical_item["manager_job_id"])
            item.update(physical_item)
            item["id"] = row["id"]
        item["title"] = manifest.get("title") or item.get("title") or path.parent.name
        item["barcode"] = row["barcode"] or item.get("barcode")
        item["year"] = manifest.get("year") or item.get("year")
        item["media_type"] = manifest.get("media_type") or item.get("media_type")
        if not item["media_type"]:
            for kind, relative in folders.items():
                if path.is_relative_to((destination / relative).resolve()):
                    item["media_type"] = kind
                    break
        item["ownership"] = "manifest"
        item["location_status"] = "In library"
        result.append(item)
    for item in physical:
        if item["manager_job_id"] in used:
            continue
        item = dict(item)
        item.update(id="rip:" + item["manager_job_id"], ownership="rip", manifest_path=None)
        item["location_status"] = ("In library" if item.get("final_dir") or item.get("node_id") == "Byte-Me"
                                   else "Awaiting transfer" if item.get("state") == "complete"
                                   else "On ripping node")
        result.append(item)
    barcodes = {item["barcode"] for item in result if item.get("barcode")}
    for row in db.query("SELECT id,title,barcode FROM owned_upcs"):
        if row["barcode"] not in barcodes:
            result.append({"id": f"upc:{row['id']}", "title": row["title"] or "Owned — metadata pending",
                           "barcode": row["barcode"], "manifest_path": None, "media_type": None,
                           "ownership": "owned_upc", "location_status": "Owned UPC"})
    return sorted([_enrich(item) for item in result], key=lambda item: str(item.get("display_title") or item.get("title") or "").casefold())


def fuzzy_search(title: str, limit: int = 12, media_type: str = "") -> list[dict]:
    needle = _title_key(title)
    if not needle:
        return []
    nwords = set(needle.split())
    ranked = []
    for item in _collection():
        if media_type and item.get("media_type") != media_type:
            continue
        candidate = _title_key(item.get("display_title") or item.get("title"))
        if not candidate:
            continue
        seq = SequenceMatcher(None, needle, candidate).ratio()
        cwords = set(candidate.split())
        overlap = len(nwords & cwords) / max(1, len(nwords | cwords))
        contains = needle in candidate or candidate in needle
        score = max(seq, (seq + overlap) / 2, 0.92 if contains else 0)
        if score >= 0.38:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("title") or "").casefold()))
    return [item | {"match_score": round(score, 3)} for score, item in ranked[:limit]]


def search(query: str = "", media_type: str = "", **_ignored) -> list[dict]:
    q = (query or "").strip()
    if q and not q.isdigit():
        return fuzzy_search(q, limit=10000, media_type=media_type)
    return [item for item in _collection()
            if (not media_type or item.get("media_type") == media_type)
            and (not q or item.get("barcode") == q
                 or (len(q) == 4 and (str(item.get("year") or "") == q or q in str(item.get("title") or ""))))]

def resolve_owned_upc(barcode: str, title: str) -> dict:
    code=_clean_barcode(barcode); title=str(title or "").strip()
    if not code or not title: raise ValueError("UPC and title are required")
    now=time.time()
    with db.write() as conn:
        conn.execute("""INSERT INTO owned_upcs(barcode,title,created_at) VALUES (?,?,?)
                        ON CONFLICT(barcode) DO UPDATE SET title=excluded.title""",(code,title,now))
        row=conn.execute("SELECT id,barcode,title FROM owned_upcs WHERE barcode=?",(code,)).fetchone()
    return {"id":f"upc:{row['id']}","barcode":row["barcode"],"title":row["title"],"ownership":"owned_upc"}

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

def ensure_physical(item_id):
    """Give manifest-only titles editable photos/notes using the scan's folder identity."""
    import hashlib
    item = get_item(item_id)
    if not item or not item.get("manifest_path"):
        return item
    if item.get("manager_job_id"):
        return item
    folder = _media_folder(item)
    if folder is None or not folder.is_dir():
        raise ValueError("Media folder is unavailable")
    identity = hashlib.sha256(str(folder).encode("utf-8")).hexdigest()[:24]
    job_id = "existing:" + identity
    now = time.time()
    with db.write() as conn:
        conn.execute("""INSERT OR IGNORE INTO jobs_history
                        (manager_job_id,node_id,node_job_id,drive,state,title,year,barcode,media_type,
                         output_dir,progress,cleared,updated_at)
                        VALUES (?,'Byte-Me',?,'ARCHIVE','complete',?,?,?,?,?,100,1,?)""",
                     (job_id, identity, item["title"], item.get("year"), item.get("barcode"),
                      item.get("media_type"), str(folder), now))
        conn.execute("""INSERT OR IGNORE INTO physical_media
                        (manager_job_id,final_dir,created_at,updated_at) VALUES (?,?,?,?)""",
                     (job_id, str(folder), now, now))
    return get_item(item_id)


def get_item(item_id):
    if isinstance(item_id, str) and item_id.startswith("rip:"):
        item = next((item for item in _collection() if item["id"] == item_id), None)
        if item:
            import archive
            folder = archive.existing_folder(item)
            item["manifest"] = _read_manifest(folder / "disc-info.json") if folder else None
        return item
    if isinstance(item_id,str) and item_id.startswith("upc:"):
        try: row=db.query_one("SELECT barcode,title FROM owned_upcs WHERE id=?",(int(item_id.split(":",1)[1]),))
        except ValueError: row=None
        return {"title":row["title"] or "Owned — metadata pending","barcode":row["barcode"],"manifest":None,"ownership":"owned_upc"} if row else None
    try:
        numeric_id = int(item_id)
    except (TypeError, ValueError):
        return None
    item = next((item for item in _collection() if item["id"] == numeric_id), None)
    if not item:
        return None
    item["manifest"] = _read_manifest(Path(item["manifest_path"]))
    return item
