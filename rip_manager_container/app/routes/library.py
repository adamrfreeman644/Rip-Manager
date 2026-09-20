from fastapi import APIRouter
from pydantic import BaseModel
import library

router=APIRouter(prefix="/library",tags=["library"])

class BulkImport(BaseModel):
    upcs: str

@router.get("")
def list_library(q: str=""):
    return {"items":library.search(q)}

@router.post("/bulk")
def bulk(req: BulkImport):
    return library.bulk_add(req.upcs)

@router.post("/check-manifests")
def manifests():
    return library.check_manifests()
