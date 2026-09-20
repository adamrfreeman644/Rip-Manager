import json, time

def fresh(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"db.sqlite")
    old=getattr(db._local,"conn",None)
    if old: old.close()
    db._local.conn=None; db._settings_cache.clear(); db.init_db(); return db

def test_bulk_upc_import_and_exact_lookup(tmp_path,monkeypatch):
    db=fresh(tmp_path,monkeypatch); import library
    result=library.bulk_add("001234567890\n5012345678900\n001234567890\nbad")
    assert result["added"]==2 and result["duplicates"]==1 and result["invalid"]==1
    rows=library.search("001234567890")
    assert len(rows)==1 and rows[0]["barcode"]=="001234567890"

def test_manifest_check_rebuilds_index_and_extras_flag(tmp_path,monkeypatch):
    db=fresh(tmp_path,monkeypatch); import library
    root=tmp_path/"media"; folder=root/"1 ~ Movies"/"Film"; (folder/"extras").mkdir(parents=True)
    (folder/"extras"/"trailer.mkv").write_bytes(b"x")
    payload={"manager_job_id":None,"title":"Film","year":1999,"upc":"000123456789",
             "media_type":"movie","files":[{"path":"Film.mkv"},{"path":"extras/trailer.mkv"}]}
    (folder/"disc-info.json").write_text(json.dumps(payload))
    db.set_settings({"mover_destination_root":str(root),"mover_destination_folders":json.dumps({"movie":"1 ~ Movies"})})
    result=library.check_manifests()
    assert result["database_updated"]==1
    item=library.search("000123456789")[0]
    assert item["title"]=="Film" and item["has_extras"] is True
