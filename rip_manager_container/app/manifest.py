"""Human-readable library manifests and media history."""
from __future__ import annotations
import hashlib,json,os,subprocess,time
from datetime import datetime,timezone
from pathlib import Path

MANIFEST_NAME="rip-manager-manifest.json"
MEDIA_EXTENSIONS={".mkv",".mp4",".m4v",".avi",".mov",".wmv",".webm",".flac",".mp3",".m4a",".aac"}
SIDECARS={"disc-info.txt","disc-info.json",MANIFEST_NAME,"rip-manager-prep.json"}

def _iso(ts=None):
    if ts is None: ts=time.time()
    return datetime.fromtimestamp(float(ts),timezone.utc).isoformat()

def _probe(path):
    try:
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=format_name,duration:stream=index,codec_type,codec_name,profile,language:stream_tags=language,title","-of","json",str(path)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,check=False)
        if p.returncode: return {"available":False,"error":(p.stderr or "").strip()[:500]}
        data=json.loads(p.stdout or "{}");fmt=data.get("format") or {};streams=[]
        for x in data.get("streams") or []:
            tags=x.get("tags") or {}
            streams.append({"index":x.get("index"),"type":x.get("codec_type"),"codec":x.get("codec_name"),"profile":x.get("profile"),"language":x.get("language") or tags.get("language"),"title":tags.get("title")})
        return {"available":True,"container":fmt.get("format_name"),"duration_seconds":float(fmt.get("duration") or 0),"streams":streams}
    except (OSError,ValueError,json.JSONDecodeError,subprocess.TimeoutExpired) as exc:
        return {"available":False,"error":str(exc)}

def inspect_file(path,root=None,source="filesystem"):
    st=path.stat();root=root or path.parent
    return {"id":hashlib.sha256(str(path.relative_to(root)).encode()).hexdigest()[:16],
            "path":str(path.relative_to(root)),"filename":path.name,"bytes":st.st_size,
            "filesystem":{"created_or_changed":_iso(st.st_ctime),"modified":_iso(st.st_mtime)},
            "media":_probe(path),"provenance":{"source":source,"original_rip":"unknown" if source=="existing_library" else source},
            "history":[{"at":_iso(),"event":"discovered","path":str(path.relative_to(root)),"source":source}]}

def _empty(root):
    return {"schema_version":1,"managed_by":"Rip Manager","root":str(root),"created_at":_iso(),"updated_at":_iso(),"media":[],"physical_discs":[],"events":[]}

def load(root):
    p=Path(root)/MANIFEST_NAME
    if not p.is_file(): return _empty(Path(root))
    try:return json.loads(p.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): return _empty(Path(root))

def save(root,data):
    root=Path(root);data["updated_at"]=_iso();p=root/MANIFEST_NAME;tmp=root/("."+MANIFEST_NAME+".tmp")
    tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n",encoding="utf-8");os.replace(tmp,p);return p

def bootstrap(root):
    root=Path(root);data=load(root);known={x.get("path") for x in data.get("media",[])};added=0
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS and str(p.relative_to(root)) not in known:
            data.setdefault("media",[]).append(inspect_file(p,root,"existing_library"));added+=1
    if added or not (root/MANIFEST_NAME).exists():
        data.setdefault("events",[]).append({"at":_iso(),"event":"library_bootstrap","files_added":added})
        save(root,data)
    return {"files_added":added,"files_total":len(data.get("media",[]))}

def record_move(destination,job,source_files,mappings,prep=None):
    destination=Path(destination);data=load(destination)
    # If this folder predates manifests, capture existing media first.
    bootstrap(destination);data=load(destination)
    disc={"manager_job_id":job.get("manager_job_id"),"season":job.get("season"),"disc":job.get("disc"),
          "node":job.get("node_id"),"drive":job.get("drive"),"rip_started":job.get("started_at"),
          "rip_finished":job.get("finished_at"),"verification":job.get("verification"),"prep":prep or {}}
    if not any(x.get("manager_job_id")==disc["manager_job_id"] for x in data.setdefault("physical_discs",[])):
        data["physical_discs"].append(disc)
    by_path={x.get("path"):x for x in data.setdefault("media",[])}
    now=_iso()
    for source_rel,dest_rel in mappings:
        item=by_path.get(dest_rel)
        if item is None:
            target=destination/dest_rel
            item=inspect_file(target,destination,"rip_manager")
            data["media"].append(item);by_path[dest_rel]=item
        item["provenance"].update({"manager_job_id":job.get("manager_job_id"),"node":job.get("node_id"),"drive":job.get("drive"),"season":job.get("season"),"disc":job.get("disc"),"source_path":source_rel})
        item.setdefault("history",[]).append({"at":now,"event":"moved","from":source_rel,"to":dest_rel,"manager_job_id":job.get("manager_job_id")})
    data.setdefault("events",[]).append({"at":now,"event":"mover_merge","manager_job_id":job.get("manager_job_id"),"files":len(mappings)})
    save(destination,data);return data

def bootstrap_library(root,folders):
    root=Path(root).resolve();result={"folders_seen":0,"manifests_created":0,"files_indexed":0,"errors":[]}
    for rel in folders.values():
        base=(root/rel).resolve()
        try:base.relative_to(root)
        except ValueError:continue
        if not base.is_dir():continue
        candidates={p.parent for p in base.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS}
        for folder in sorted(candidates):
            result["folders_seen"]+=1;existed=(folder/MANIFEST_NAME).is_file()
            try:
                r=bootstrap(folder);result["files_indexed"]+=r["files_added"];result["manifests_created"]+=0 if existed else 1
            except OSError as exc:result["errors"].append({"folder":str(folder),"error":str(exc)})
    result["ok"]=not result["errors"];return result
