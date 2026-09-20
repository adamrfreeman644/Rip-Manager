import json

def fresh(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"db.sqlite")
    old=getattr(db._local,"conn",None)
    if old: old.close()
    db._local.conn=None; db._settings_cache.clear(); db.init_db(); return db

def test_bulk_upcs_live_separately_from_manifest_index(tmp_path,monkeypatch):
    db=fresh(tmp_path,monkeypatch); import library
    result=library.bulk_add("001234567890\n5012345678900\n001234567890\nbad")
    assert result["added"]==2 and result["duplicates"]==1 and result["invalid"]==1
    assert db.query_one("SELECT COUNT(*) n FROM library_items")["n"]==0
    assert db.query_one("SELECT COUNT(*) n FROM owned_upcs")["n"]==2
    rows=library.search("001234567890")
    assert len(rows)==1 and rows[0]["title"]=="Owned — metadata pending"

def test_manifest_index_only_stores_lookup_fields_and_reads_json_live(tmp_path,monkeypatch):
    db=fresh(tmp_path,monkeypatch); import library
    root=tmp_path/"media"; folder=root/"1 ~ Movies"/"Film (1999)"; folder.mkdir(parents=True)
    manifest=folder/"disc-info.json"
    payload={"title":"Film","year":1999,"upc":"000123456789","media_type":"movie",
             "summary":{"total_duration_seconds":7200},"files":[{"path":"extras/trailer.mkv"}]}
    manifest.write_text(json.dumps(payload))
    db.set_settings({"mover_destination_root":str(root),"mover_destination_folders":json.dumps({"movie":"1 ~ Movies"})})
    result=library.check_manifests(); assert result["database_updated"]==1
    row=db.query_one("SELECT * FROM library_items")
    assert set(row.keys())=={"id","title","barcode","manifest_path","created_at","updated_at"}
    assert row["manifest_path"]==str(manifest)
    item=library.get_item(row["id"])
    assert item["manifest"]["year"]==1999
    payload["year"]=2000; manifest.write_text(json.dumps(payload))
    assert library.get_item(row["id"])["manifest"]["year"]==2000


def test_fuzzy_title_and_resolved_owned_upc(tmp_path,monkeypatch):
    db=fresh(tmp_path,monkeypatch); import library
    root=tmp_path/"media"; folder=root/"Movies"/"The Fellowship of the Ring (2001)"; folder.mkdir(parents=True)
    manifest=folder/"disc-info.json"; manifest.write_text(json.dumps({"title":"The Lord of the Rings - The Fellowship of the Ring","year":2001}))
    db.set_settings({"mover_destination_root":str(root),"mover_destination_folders":json.dumps({"movie":"Movies"})})
    library.check_manifests()
    matches=library.fuzzy_search("Lord Rings Fellowship Ring 2 Disc DVD")
    assert matches and "Fellowship" in matches[0]["title"]
    owned=library.resolve_owned_upc("5039036040631",matches[0]["title"])
    assert owned["title"]==matches[0]["title"]
    assert library.search("5039036040631")[0]["ownership"]=="owned_upc"
