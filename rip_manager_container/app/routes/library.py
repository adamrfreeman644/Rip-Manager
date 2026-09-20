from fastapi import APIRouter
from pydantic import BaseModel
import library

router=APIRouter(prefix="/library",tags=["library"])

class BulkImport(BaseModel):
    upcs: str

@router.get("")
def list_library(q: str="", media_type: str="", extras: str="", sort: str="title"):
    return {"items":library.search(q, media_type, extras, sort)}

@router.post("/bulk")
def bulk(req: BulkImport):
    return library.bulk_add(req.upcs)

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
