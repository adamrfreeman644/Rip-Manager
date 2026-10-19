from fastapi import APIRouter
from pydantic import BaseModel
import library
import archive
import upc
import recovery
import media_intake
import library_maintenance

router=APIRouter(prefix="/api/collection",tags=["library"])

class BulkImport(BaseModel):
    upcs: str

class ResolveUPC(BaseModel):
    barcode: str
    title: str

@router.get("")
def list_library(q: str="", media_type: str="", extras: str="", sort: str="title"):
    job=library_maintenance.ensure_initial_index()
    return {"items":library.search(q, media_type=media_type, extras=extras, sort=sort),
            "index_job":job,"indexing":bool(job and job.get("status")=="running")}

@router.post("/bulk")
def bulk(req: BulkImport):
    return library.bulk_add(req.upcs)

@router.get("/smart/{code}")
async def smart_lookup(code: str, media_type: str=""):
    job=library_maintenance.ensure_initial_index()
    if job and job.get("status")!="complete":
        # Never fall through to an external UPC provider before the owned index
        # is ready: an unfinished initial scan does not mean the media is new.
        return {"state":"indexing","items":library.search(code,media_type=media_type),
                "index_job":job,"indexing":job.get("status")=="running"}
    # Owned always wins: never call an external provider for a UPC we already know.
    owned=library.search(code, media_type=media_type)
    if owned:
        return {"state":"owned","items":owned}
    result=await upc.lookup(code)
    if not result.get("found"):
        # A failed metadata lookup must not block manual matching.  Return the
        # collection with a null detected title so the UI can keep the scanned
        # UPC pending while the user searches by title.
        return {"state":"new_unresolved","lookup":result,"detected_title":None,
                "matches":library.search(media_type=media_type)}
    lookup_matches=result.get("matches") or []
    title=lookup_matches[0].get("title") if lookup_matches else None
    return {"state":"new","lookup":result,"detected_title":title,
            "matches":library.fuzzy_search(title, media_type=media_type) if title else []}

@router.post("/resolve-upc")
def resolve_upc(req: ResolveUPC):
    return library.resolve_owned_upc(req.barcode,req.title)

@router.post("/check-manifests")
def manifests():
    return library.check_manifests()

@router.get("/manifest-backfill")
def manifest_backfill_preview():
    return archive.backfill_missing_manifests(preview=True)

@router.post("/manifest-backfill")
def manifest_backfill_create():
    result = archive.backfill_missing_manifests(preview=False)
    library.check_manifests()
    return result

@router.get("/health-scan")
def health_scan():
    return library_maintenance.health()


@router.post("/health-repair")
def health_repair():
    return library_maintenance.repair()


class MaintenanceRequest(BaseModel):
    kind: str


@router.post("/maintenance")
def start_maintenance(req: MaintenanceRequest):
    from fastapi import HTTPException
    try:
        return library_maintenance.start(req.kind)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@router.get("/maintenance")
def maintenance_status():
    return library_maintenance.status()


@router.post("/{item_id}/physical")
def attach_physical(item_id: str):
    from fastapi import HTTPException
    try:
        item = library.ensure_physical(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Library item not found")
    return item

@router.get("/{item_id}/artwork")
def library_artwork(item_id: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    item = library.get_item(item_id)
    path = library.artwork_path(item) if item else None
    if path is None:
        raise HTTPException(status_code=404, detail="No local artwork found")
    return FileResponse(path)

@router.get("/{item_id}")
def library_item(item_id: str):
    item=library.get_item(item_id)
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404,detail="Library item not found")
    return item
