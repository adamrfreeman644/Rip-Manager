"""GitHub release discovery and explicit manual install requests."""
from __future__ import annotations
import json, re, tarfile, time, urllib.error, urllib.request
from fastapi import HTTPException
from config import BUNDLED_NODE_FILE, BUNDLED_NODE_FILENAME, BUNDLED_NODE_VERSION, GITHUB_MANAGER_REPO, GITHUB_OWNER, GITHUB_TOKEN_FILE, UPDATE_DIR

def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", value or "")[:4]) or (0,)

def _token() -> str:
    try: return GITHUB_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError: return ""

def latest_release(repo: str) -> dict:
    token = _token()
    if not token: raise HTTPException(status_code=503, detail="GitHub token is not configured")
    request = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}/releases/latest", headers={"Accept":"application/vnd.github+json","Authorization":f"Bearer {token}","X-GitHub-Api-Version":"2022-11-28","User-Agent":"Rip-Manager"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response: return json.load(response)
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"GitHub release check failed: {exc}") from exc

def download_asset(repo: str, asset_id: int) -> bytes:
    token=_token()
    request=urllib.request.Request(f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}/releases/assets/{asset_id}",headers={"Accept":"application/octet-stream","Authorization":f"Bearer {token}","X-GitHub-Api-Version":"2022-11-28","User-Agent":"Rip-Manager"})
    try:
        with urllib.request.urlopen(request,timeout=60) as response: return response.read()
    except (OSError,urllib.error.HTTPError) as exc:
        raise HTTPException(status_code=502,detail=f"GitHub asset download failed: {exc}") from exc

def describe_release(repo: str, installed: str | None = None) -> dict:
    release = latest_release(repo); version = str(release.get("tag_name") or "").lstrip("v")
    assets = release.get("assets") or []; asset = assets[0] if assets else {}
    return {"version":version,"filename":asset.get("name"),"bytes":asset.get("size",0),"asset_id":asset.get("id"),"newer":version_tuple(version)>version_tuple(installed or "0"),"published_at":release.get("published_at"),"source":"GitHub"}

def manager_release(installed: str | None = None) -> dict: return describe_release(GITHUB_MANAGER_REPO, installed)
def node_release() -> dict:
    """Describe the Node API shipped inside this Manager release."""
    try: size=BUNDLED_NODE_FILE.stat().st_size
    except OSError: size=0
    return {"version":BUNDLED_NODE_VERSION,"filename":BUNDLED_NODE_FILENAME,
            "bytes":size,"asset_id":None,"newer":False,"published_at":None,
            "source":"Bundled with Rip Manager"}

def _archive_version(path) -> str:
    sidecar=path.with_suffix("").with_suffix(".json")
    try:
        data=json.loads(sidecar.read_text(encoding="utf-8")); version=str(data.get("version") or "")
        if version:return version
    except (OSError,json.JSONDecodeError):pass
    try:
        with tarfile.open(path,"r:gz") as archive:
            member=next((x for x in archive.getmembers() if x.name.endswith("/app/config.py")),None)
            if not member:return "unknown"
            stream=archive.extractfile(member); text=stream.read().decode("utf-8","replace") if stream else ""
            match=re.search(r'^VERSION\s*=\s*["\']([^"\']+)',text,re.M)
            return match.group(1) if match else "unknown"
    except (OSError,tarfile.TarError):return "unknown"

def rollback_backups(limit:int=5) -> list[dict]:
    root=UPDATE_DIR/"Backups"; items=[]
    try: candidates=sorted(root.glob("rip-manager-*.tar.gz"),key=lambda p:p.stat().st_mtime,reverse=True)
    except OSError:return []
    for path in candidates:
        if not (re.fullmatch(r"rip-manager-code-v[0-9A-Za-z._-]+-\d{8}-\d{6}\.tar\.gz",path.name)
                or re.fullmatch(r"rip-manager-\d{8}-\d{6}\.tar\.gz",path.name)):continue
        if not tarfile.is_tarfile(path):continue
        try: stat=path.stat()
        except OSError:continue
        items.append({"filename":path.name,"version":_archive_version(path),"created_at":stat.st_mtime,
                      "bytes":stat.st_size,"verified":True,"restores_code_only":True})
        if len(items)>=limit:break
    return items

def host_capabilities() -> dict:
    return read_json("State/host-updater-capabilities.json") or {"version":"1","rollback":False,"code_only_backups":False}

def write_request(filename: str, payload: dict) -> None:
    try:
        path=UPDATE_DIR/filename; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(payload),encoding="utf-8")
    except OSError as exc: raise HTTPException(status_code=500,detail=f"Could not queue the install: {exc}") from exc

def read_json(filename: str) -> dict | None:
    path=UPDATE_DIR/filename
    try: return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError,json.JSONDecodeError): return None

def clear_request(filename: str) -> None:
    try: (UPDATE_DIR/filename).unlink(missing_ok=True)
    except OSError: pass

def new_request_id() -> str: return str(int(time.time()*1000))
