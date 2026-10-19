"""Discovery and safe adoption of stranded Rip Node folders into the standard Mover."""
from __future__ import annotations
import hashlib, json, time
from pathlib import Path
import archive, db, mover

MEDIA_ROOTS={"movie":"Movies","tv":"TV","music":"Music","audiobook":"AudioBooks"}
MEDIA_EXT={
 "movie":{".mkv",".mp4",".avi",".m4v",".mov",".wmv",".webm"},
 "tv":{".mkv",".mp4",".avi",".m4v",".mov",".wmv",".webm"},
 "music":{".flac",".mp3",".m4a",".aac",".wav",".ogg",".opus"},
 "audiobook":{".flac",".mp3",".m4a",".m4b",".aac",".wav",".ogg",".opus"},
}

def _under(path,root):
    try:path.resolve().relative_to(root.resolve());return True
    except ValueError:return False

def _mount(node_id):
    value=db.get_setting_json("mover_node_mounts",{}).get(node_id)
    if not value:raise ValueError(f"No mover mount configured for {node_id}")
    root=Path(value).resolve()
    if not root.is_dir():raise ValueError(f"Rip Node mount unavailable: {root}")
    return root

def _media_root(node_id,media_type):
    root=_mount(node_id); name=MEDIA_ROOTS[media_type]
    for candidate in (root/"Rips"/name,root/name):
        if candidate.is_dir():return candidate.resolve()
    raise ValueError(f"{name} root not found on {node_id}")

def _units(node_id,media_type):
    root=_media_root(node_id,media_type)
    # A direct child of a media root is one recovery unit. This keeps TV seasons,
    # discs, albums, subtitles, artwork and sidecars together.
    for folder in sorted((p for p in root.iterdir() if p.is_dir()),key=lambda p:p.name.casefold()):
        if any(p.is_file() and p.suffix.lower() in MEDIA_EXT[media_type] for p in folder.rglob("*")):
            yield folder

def _destination(folder,media_type):
    root=Path(db.get_setting("mover_destination_root","/media")).resolve()
    rel=db.get_setting_json("mover_destination_folders",{}).get(media_type)
    if not rel:raise ValueError(f"No destination configured for {media_type}")
    parent=(root/rel).resolve()
    if not _under(parent,root):raise ValueError("Unsafe recovery destination")
    return parent/Path(folder).name

def discover():
    out=[]
    for node_id in db.get_setting_json("mover_node_mounts",{}):
        for media_type in MEDIA_ROOTS:
            try:folders=list(_units(node_id,media_type))
            except (OSError,ValueError):continue
            for folder in folders:
                files=[p for p in folder.rglob("*") if p.is_file()]
                destination=_destination(folder,media_type)
                out.append({"node_id":node_id,"folder":folder.name,"media_type":media_type,
                            "files":len(files),"bytes":sum(p.stat().st_size for p in files),
                            "destination":str(destination),"destination_exists":destination.exists(),
                            "manifest":(folder/"disc-info.json").is_file()})
    return out

def _source(node_id,folder,media_type="movie"):
    if media_type not in MEDIA_ROOTS:raise ValueError("Unsupported media type")
    if not folder or folder in {".",".."} or "/" in folder or "\\" in folder:raise ValueError("Unsafe folder name")
    root=_media_root(node_id,media_type);source=(root/folder).resolve()
    if not _under(source,root) or source==root or not source.is_dir():raise ValueError("Existing rip folder not found")
    return source

def analyse(node_id,folder,media_type="movie"):
    source=_source(node_id,folder,media_type)
    files=[p for p in source.rglob("*") if p.is_file()]
    if not files:raise ValueError("Folder is empty")
    destination=_destination(folder,media_type)
    return {"node_id":node_id,"folder":folder,"media_type":media_type,
            "destination":str(destination),"destination_exists":destination.exists(),
            "files":[{"path":p.relative_to(source).as_posix(),"bytes":p.stat().st_size,
                      "classification":"preserve"} for p in files],
            "ready":not destination.exists()}

def _content_identity(source,node_id,media_type):
    """Identify this generation of a recovery folder from its complete contents."""
    entries=[]
    for path in sorted((p for p in source.rglob("*") if p.is_file()),key=lambda p:p.relative_to(source).as_posix()):
        # The generated manifest is metadata about the adoption, not source content.
        if path.relative_to(source).as_posix() == "disc-info.json":
            continue
        stat=path.stat()
        entries.append((path.relative_to(source).as_posix(),stat.st_size,stat.st_mtime_ns))
    payload=json.dumps({"node":node_id,"media_type":media_type,"source":str(source),"files":entries},separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]

def adopt(node_id,folder,media_type="movie"):
    preview=analyse(node_id,folder,media_type)
    if preview["destination_exists"]:raise FileExistsError("Destination already exists; recovery stopped")
    source=_source(node_id,folder,media_type)
    archive.write_recovery_manifest(source,media_type=media_type,node_id=node_id)
    identity=_content_identity(source,node_id,media_type)
    manager_job_id="recovery:"+identity; now=time.time()
    # output_dir is the path as seen on the node. Reconstruct it from the configured
    # node source root so the standard Mover resolves it through the mounted share.
    mounted_root=_mount(node_id)
    relative=source.relative_to(mounted_root)
    node_root=Path(db.get_setting_json("mover_node_source_roots",{}).get(node_id,"/mnt/ripping"))
    output_dir=node_root/relative
    with db.write() as conn:
        conn.execute("""INSERT OR IGNORE INTO jobs_history(
          manager_job_id,node_id,node_job_id,drive,state,title,media_type,started_at,finished_at,
          progress,last_message,output_dir,verification_json,raw_json,auto_started,cleared,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (manager_job_id,node_id,identity,"RECOVERY","complete",folder,media_type,now,now,100,
           "Existing rip adopted for verified transfer",str(output_dir),
           json.dumps({"ok":True,"source":"recovery"}),"{}",0,0,now))
        conn.execute("""INSERT OR IGNORE INTO physical_media(manager_job_id,final_dir,created_at,updated_at)
                       VALUES (?,?,?,?)""",(manager_job_id,str(source),now,now))
    if not mover.enqueue(manager_job_id):
        row=db.query_one("SELECT state FROM transfer_queue WHERE manager_job_id=?",(manager_job_id,))
        if not row:raise ValueError("Mover is disabled; enable it before adopting existing rips")
    return {"ok":True,"queued":True,"manager_job_id":manager_job_id,**preview}

# Backward-compatible name for the existing API while the UI migrates.
def recover(node_id,folder,delete_source=True,media_type="movie"):
    if delete_source is False:
        raise ValueError("Recovery now uses the standard Mover; source deletion follows the Mover setting")
    return adopt(node_id,folder,media_type)
