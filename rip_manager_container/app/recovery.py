"""Safe discovery, preview and verified recovery of existing movie rips."""
from __future__ import annotations
import hashlib, os, re, shutil, subprocess, time
from pathlib import Path
import db
import archive

VIDEO_EXTENSIONS={".mkv",".mp4",".avi",".m4v",".mov",".wmv",".webm"}

def _under(path,root):
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False

def _duration(path):
    try:
        r=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],
                         text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,check=False)
        return float((r.stdout or "0").strip() or 0) if r.returncode==0 else 0.0
    except (OSError,ValueError,subprocess.TimeoutExpired): return 0.0

def _mount(node_id):
    mounts=db.get_setting_json("mover_node_mounts",{})
    value=mounts.get(node_id)
    if not value: raise ValueError(f"No mover mount configured for {node_id}")
    root=Path(value).resolve()
    if not root.is_dir(): raise ValueError(f"Rip Node mount unavailable: {root}")
    return root

def _movie_root(node_id):
    root=_mount(node_id)
    candidates=[root/"Rips"/"Movies",root/"Movies",root]
    for p in candidates:
        if p.is_dir() and p!=root: return p
    return root

def discover():
    out=[]
    for node_id,mount in db.get_setting_json("mover_node_mounts",{}).items():
        try: root=_movie_root(node_id)
        except (OSError,ValueError): continue
        for folder in sorted((p for p in root.iterdir() if p.is_dir()),key=lambda p:p.name.lower()):
            videos=[p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
            if not videos: continue
            destination=_destination(folder.name)
            out.append({"node_id":node_id,"folder":folder.name,"files":len(videos),
                        "bytes":sum(p.stat().st_size for p in videos),"destination":str(destination),
                        "destination_exists":destination.exists()})
    return out

def _source(node_id,folder):
    if not folder or folder in {".",".."} or "/" in folder or "\\" in folder: raise ValueError("Unsafe folder name")
    root=_movie_root(node_id).resolve(); source=(root/folder).resolve()
    if not _under(source,root) or source==root or not source.is_dir(): raise ValueError("Existing rip folder not found")
    return source

def _destination(folder):
    root=Path(db.get_setting("mover_destination_root","/media")).resolve()
    rel=db.get_setting_json("mover_destination_folders",{}).get("movie","1 ~ Movies")
    parent=(root/rel).resolve()
    if not _under(parent,root): raise ValueError("Unsafe movie destination")
    return parent/folder

def analyse(node_id,folder):
    source=_source(node_id,folder)
    files=sorted((p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS),key=lambda p:str(p).lower())
    if not files: raise ValueError("No video files found")
    rows=[]
    for p in files:
        d=_duration(p); rows.append({"path":p.relative_to(source).as_posix(),"duration":d,"bytes":p.stat().st_size})
    main=max(rows,key=lambda x:(x["duration"],x["bytes"]))
    longest=main["duration"]
    for row in rows:
        row["classification"]="main" if row is main else ("extra" if longest>0 and row["duration"]<longest*.75 else "review")
    return {"node_id":node_id,"folder":folder,"destination":str(_destination(folder)),
            "destination_exists":_destination(folder).exists(),"files":rows,
            "ready":not _destination(folder).exists() and all(x["classification"]!="review" for x in rows)}

def _hash(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def recover(node_id,folder,delete_source=True):
    preview=analyse(node_id,folder)
    if preview["destination_exists"]: raise FileExistsError("Destination already exists; recovery stopped")
    review=[x for x in preview["files"] if x["classification"]=="review"]
    if review: raise ValueError("Analysis has REVIEW files; recovery stopped until they are resolved")
    source=_source(node_id,folder)
    # Persist the same schema-v2 JSON used by normal Rip Manager jobs while the
    # media is still on the Rip Node. It is then included in verified copying.
    manifest_status=archive.write_recovery_manifest(source, media_type="movie", node_id=node_id)
    if not (source/"disc-info.json").is_file():
        raise ValueError("Rip Node manifest could not be created")
    destination=_destination(folder); partial=destination.with_name("."+destination.name+".recovery-partial")
    destination.parent.mkdir(parents=True,exist_ok=True)
    if partial.exists(): shutil.rmtree(partial)
    partial.mkdir()
    main=next(x for x in preview["files"] if x["classification"]=="main")
    title=re.sub(r'[\\/:*?"<>|]+'," ",folder).strip()
    copied=[]
    try:
        for idx,row in enumerate(preview["files"],1):
            src=source/row["path"]; ext=src.suffix.lower()
            if row["classification"]=="main": rel=Path(f"{title}{ext}")
            else: rel=Path("extras")/f"{title} Extra {idx:02d}{ext}"
            dst=partial/rel; dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(src,dst)
            if dst.stat().st_size!=src.stat().st_size or _hash(dst)!=_hash(src): raise IOError(f"Verification failed: {row['path']}")
            copied.append({"source":row["path"],"destination":rel.as_posix(),"bytes":row["bytes"]})
        os.replace(partial,destination)
    except Exception:
        if partial.exists(): shutil.rmtree(partial)
        raise
    source_removed=False
    if delete_source:
        shutil.rmtree(source); source_removed=True
    return {"ok":True,"folder":folder,"destination":str(destination),"files":copied,
            "source_removed":source_removed,"verified":True,"manifest":manifest_status,"finished_at":time.time()}
