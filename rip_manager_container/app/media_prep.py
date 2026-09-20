"""Post-rip media preparation used by the mover."""
from __future__ import annotations
import json,re,shutil,subprocess
from pathlib import Path
VIDEO_EXTENSIONS={".mkv",".mp4",".avi",".m4v",".mov",".wmv",".webm"}
def _duration(path):
    try:
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,check=False)
        return float((p.stdout or "0").strip() or 0) if p.returncode==0 else 0.0
    except (OSError,ValueError,subprocess.TimeoutExpired): return 0.0
def _videos(root):
    return sorted((p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS),key=lambda p:str(p).lower())
def _safe(text): return re.sub(r'[\\/:*?"<>|]+'," ",text).strip() or "Untitled"
def _play_all(files,durations):
    positive=[p for p in files if durations.get(p,0)>0]
    if len(positive)<3:return None
    longest=max(positive,key=lambda p:durations[p]); others=[p for p in positive if p!=longest]
    total=sum(durations[p] for p in others); tolerance=max(20.0,total*.02)
    return longest if total>0 and abs(durations[longest]-total)<=tolerance else None
def prepare_completed_rip(source,job):
    source=Path(source); marker=source/"disc-info.txt"
    if job.get("state")!="complete": raise ValueError("Media prep requires a completed rip")
    if not marker.is_file(): raise ValueError("Media prep marker disc-info.txt is missing")
    files=_videos(source)
    if not files:return {"ok":True,"prepared":0,"extras":0,"play_all_removed":0}
    media_type=(job.get("media_type") or "").lower()
    if media_type not in {"tv","movie"}:return {"ok":True,"prepared":0,"extras":0,"play_all_removed":0}
    durations={p:_duration(p) for p in files}; title=_safe(job.get("title") or source.name)
    disc=int(job.get("disc") or 1); season=int(job.get("season") or 0); removed=0
    if media_type=="tv":
        play_all=_play_all(files,durations)
        if play_all:
            play_all.unlink();files.remove(play_all);durations.pop(play_all,None);removed=1
    extras=set()
    if files:
        longest=max((durations.get(p,0) for p in files),default=0)
        if longest>0:
            if media_type=="movie":
                main=max(files,key=lambda p:durations.get(p,0))
                extras={p for p in files if p!=main and durations.get(p,0)<longest*.75}
            else:
                extras={p for p in files if durations.get(p,0)>0 and durations[p]<longest*.70}
    extras_dir=source/"extras";prepared=0
    for number,path in enumerate(list(files),1):
        suffix=path.suffix.lower()
        stem=(f"{title} S{season:02d} D{disc:02d}F{number:02d}" if media_type=="tv" else
              (title if path not in extras and number==1 else f"{title} D{disc:02d}F{number:02d}"))
        target_dir=extras_dir if path in extras else source;target_dir.mkdir(parents=True,exist_ok=True)
        target=target_dir/f"{stem}{suffix}";n=2
        while target.exists() and target.resolve()!=path.resolve():
            target=target_dir/f"{stem}-{n}{suffix}";n+=1
        if path.resolve()!=target.resolve(): shutil.move(str(path),str(target))
        prepared+=1
    for folder in sorted((p for p in source.rglob("*") if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
        try:folder.rmdir()
        except OSError:pass
    report={"ok":True,"prepared":prepared,"extras":len(extras),"play_all_removed":removed,"media_type":media_type,"season":season if media_type=="tv" else None,"disc":disc}
    (source/"rip-manager-prep.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report
