"""Digital/download intake staging. Each top-level item is independently prepared and queued."""
from __future__ import annotations
from pathlib import Path
import hashlib, json, time, uuid
import archive, db

MEDIA_DIRS={"movie":"Movies","tv":"TV","music":"Music","audiobook":"Audiobooks"}
MEDIA_EXT={".mkv",".mp4",".m4v",".avi",".mov",".wmv",".webm",".ts",".m2ts",
           ".flac",".mp3",".m4a",".m4b",".aac",".wav",".ogg",".opus"}

def root():
    media=Path(db.get_setting("mover_destination_root","/media")).resolve()
    return (media/"0 ~ Intake").resolve()

def ensure():
    base=root()
    for name in MEDIA_DIRS.values():(base/name).mkdir(parents=True,exist_ok=True)
    return base

def _safe(value):
    name="".join(ch for ch in str(value).strip() if ch not in '/\\:*?"<>|').strip(" .")[:180]
    if not name:raise ValueError("A title is required")
    return name

def folder_for(media_type,title,unique=False):
    if media_type not in MEDIA_DIRS:raise ValueError("Unsupported media type")
    section=ensure()/MEDIA_DIRS[media_type]; name=_safe(title)
    candidate=section/name
    if unique and candidate.exists():
        candidate=section/f"{name} [{uuid.uuid4().hex[:8]}]"
    return candidate

def _destination(folder,media_type):
    media=Path(db.get_setting("mover_destination_root","/media")).resolve()
    rel=db.get_setting_json("mover_destination_folders",{}).get(media_type)
    if not rel:raise ValueError(f"No destination configured for {media_type}")
    parent=(media/rel).resolve()
    if not parent.is_relative_to(media):raise ValueError("Unsafe destination")
    return parent/folder.name

def _item(folder,media_type):
    destination=_destination(folder,media_type)
    manifest=folder/"disc-info.json"
    invalid=False
    if manifest.is_file():
        try: invalid=not isinstance(json.loads(manifest.read_text(encoding="utf-8")),dict)
        except (OSError,ValueError,TypeError): invalid=True
    return {"id":hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:24],
            "folder":str(folder.resolve()),"name":folder.name,"media_type":media_type,
            "manifest":manifest.is_file() and not invalid,"invalid_manifest":invalid,
            "destination":str(destination),"destination_exists":destination.exists(),
            "needs_attention":invalid or destination.exists()}

def scan():
    base=ensure();items=[]
    for media_type,name in MEDIA_DIRS.items():
        section=base/name
        # Loose media is converted into a safe single-item folder so it can use the
        # same manifest/mover path as every other digital import.
        for loose in list(section.iterdir()):
            if loose.is_file() and loose.suffix.lower() in MEDIA_EXT:
                target=folder_for(media_type,loose.stem,unique=True);target.mkdir()
                loose.replace(target/loose.name)
        for folder in sorted((p for p in section.iterdir() if p.is_dir()),key=lambda p:p.name.casefold()):
            if any(p.is_file() and p.suffix.lower() in MEDIA_EXT for p in folder.rglob("*")):
                items.append(_item(folder,media_type))
    return {"root":str(base),"count":len(items),"items":items}

def find(item_id):
    for item in scan()["items"]:
        if item["id"]==item_id:return item
    raise KeyError(item_id)

def prepare(item_id, provenance="digital_import", source=None):
    item=find(item_id);folder=Path(item["folder"])
    if item["invalid_manifest"]:raise ValueError("Existing disc-info.json is invalid; repair it before moving")
    if item["destination_exists"]:raise FileExistsError(f"Destination already exists: {item['destination']}")
    if not item["manifest"]:
        archive.write_recovery_manifest(folder,title=folder.name,media_type=item["media_type"],node_id="Media-Intake",
                                        provenance=provenance,source=source)
    return find(item_id)

def queue(item_id, provenance="digital_import", source=None):
    import mover
    item=prepare(item_id,provenance,source);folder=Path(item["folder"]).resolve()
    # Identity includes content metadata, so reusing the same staging name later
    # cannot silently reuse an old completed intake job.
    signature=[]
    for p in sorted(x for x in folder.rglob("*") if x.is_file()):
        st=p.stat();signature.append((p.relative_to(folder).as_posix(),st.st_size,st.st_mtime_ns))
    identity=hashlib.sha256(json.dumps(signature,separators=(",",":")).encode()).hexdigest()[:24]
    job_id="intake:"+identity;now=time.time()
    with db.write() as conn:
        inserted=conn.execute("""INSERT OR IGNORE INTO jobs_history(
          manager_job_id,node_id,node_job_id,drive,state,title,media_type,started_at,finished_at,
          progress,last_message,output_dir,verification_json,raw_json,auto_started,cleared,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (job_id,"Media-Intake",identity,"INTAKE","complete",folder.name,item["media_type"],now,now,100,
           "Media intake ready",str(folder),json.dumps({"ok":True,"source":provenance}),
           json.dumps({"intake_provenance":provenance,"source":source}),0,0,now)).rowcount
        conn.execute("INSERT OR IGNORE INTO physical_media(manager_job_id,final_dir,created_at,updated_at) VALUES (?,?,?,?)",
                     (job_id,str(folder),now,now))
    if not inserted:
        row=db.query_one("SELECT state FROM transfer_queue WHERE manager_job_id=?",(job_id,))
        return {"ok":True,"queued":False,"already_adopted":True,"manager_job_id":job_id,"transfer_state":row["state"] if row else None}
    if not mover.enqueue(job_id):raise ValueError("Mover is disabled; enable it before queueing Intake")
    return {"ok":True,"queued":True,"manager_job_id":job_id}

# Compatibility helpers retained for API callers; they no longer prepare/queue unrelated items implicitly.
def prepare_all():
    result={"created":0,"existing":0,"failed":0,"errors":[]}
    for item in scan()["items"]:
        try:
            before=item["manifest"];prepare(item["id"]);result["existing" if before else "created"]+=1
        except Exception as exc:result["failed"]+=1;result["errors"].append({"folder":item["folder"],"error":str(exc)})
    return {**result,**scan()}

def queue_all():
    result={"queued":0,"already_adopted":0,"failed":0,"errors":[]}
    for item in scan()["items"]:
        try:
            r=queue(item["id"]);result["queued" if r.get("queued") else "already_adopted"]+=1
        except Exception as exc:result["failed"]+=1;result["errors"].append({"folder":item["folder"],"error":str(exc)})
    return {**result,**scan()}
