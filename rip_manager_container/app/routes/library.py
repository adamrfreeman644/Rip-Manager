from fastapi import APIRouter
from pydantic import BaseModel
import library
import upc

router=APIRouter(prefix="/api/library",tags=["library"])

class BulkImport(BaseModel):
    upcs: str

class ResolveUPC(BaseModel):
    barcode: str
    title: str

@router.get("")
def list_library(q: str="", media_type: str="", extras: str="", sort: str="title"):
    return {"items":library.search(q, media_type, extras, sort)}

@router.post("/bulk")
def bulk(req: BulkImport):
    return library.bulk_add(req.upcs)

@router.get("/smart/{code}")
async def smart_lookup(code: str):
    # Owned always wins: never call an external provider for a UPC we already know.
    owned=library.search(code)
    if owned:
        return {"state":"owned","items":owned}
    result=await upc.lookup(code)
    if not result.get("found"):
        # A failed metadata lookup must not block manual matching.  Return the
        # collection with a null detected title so the UI can keep the scanned
        # UPC pending while the user searches by title.
        return {"state":"new_unresolved","lookup":result,"detected_title":None,
                "matches":library.search()}
    lookup_matches=result.get("matches") or []
    title=lookup_matches[0].get("title") if lookup_matches else None
    return {"state":"new","lookup":result,"detected_title":title,
            "matches":library.fuzzy_search(title) if title else []}

@router.post("/resolve-upc")
def resolve_upc(req: ResolveUPC):
    return library.resolve_owned_upc(req.barcode,req.title)

@router.post("/check-manifests")
def manifests():
    return library.check_manifests()

@router.get("/{item_id}")
def library_item(item_id: str):
    item=library.get_item(item_id)
    if not item:
        from fastapi import HTTPException
        raise HTTPException(status_code=404,detail="Library item not found")
    return item
