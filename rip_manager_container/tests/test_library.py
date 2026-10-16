import json
import asyncio

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


def test_blank_search_combines_and_alphabetises_manifest_and_owned_titles(tmp_path,monkeypatch):
    db=fresh(tmp_path,monkeypatch); import library
    root=tmp_path/"media"; folder=root/"Movies"/"Zulu"; folder.mkdir(parents=True)
    (folder/"disc-info.json").write_text(json.dumps({"title":"Zulu"}))
    db.set_settings({"mover_destination_root":str(root),"mover_destination_folders":json.dumps({"movie":"Movies"})})
    library.check_manifests()
    library.resolve_owned_upc("5039036040631","Alien")

    rows=library.search()

    assert [row["title"] for row in rows]==["Alien","Zulu"]
    assert [row["ownership"] for row in rows]==["owned_upc","manifest"]
    assert library.fuzzy_search("Alien")[0]["ownership"]=="owned_upc"


def test_unresolved_upc_returns_collection_for_manual_title_search(tmp_path,monkeypatch):
    db=fresh(tmp_path,monkeypatch); import library
    from routes import library as routes
    root=tmp_path/"media"; folder=root/"Movies"/"Film (1999)"; folder.mkdir(parents=True)
    (folder/"disc-info.json").write_text(json.dumps({"title":"Film","year":1999}))
    db.set_settings({"mover_destination_root":str(root),"mover_destination_folders":json.dumps({"movie":"Movies"})})
    library.check_manifests()

    async def missing(_code): return {"found":False,"matches":[]}
    monkeypatch.setattr(routes.upc,"lookup",missing)
    result=asyncio.run(routes.smart_lookup("5039036040631"))

    assert result["state"]=="new_unresolved"
    assert result["detected_title"] is None
    assert [item["title"] for item in result["matches"]]==["Film"]


def test_collection_http_route_populates_empty_index_and_searches(tmp_path, monkeypatch):
    db = fresh(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.library import router

    folder = tmp_path / "media" / "Movies" / "Alien (1979)"
    folder.mkdir(parents=True)
    (folder / "disc-info.json").write_text(json.dumps({"title": "Alien", "year": 1979}))
    db.set_settings({"mover_destination_root": str(tmp_path / "media"),
                     "mover_destination_folders": json.dumps({"movie": "Movies"})})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for params in ({}, {"q": "Alien"}, {"q": "Alien", "media_type": "movie", "extras": "", "sort": "title"}):
            response = client.get("/api/collection", params=params)
            assert response.status_code == 200
            assert [item["title"] for item in response.json()["items"]] == ["Alien"]
        response = client.get("/api/collection/" + str(response.json()["items"][0]["id"]))
        assert response.status_code == 200
        assert response.json()["manifest"]["year"] == 1979


def add_rip(db, job_id, title, kind, folder, barcode=None):
    with db.write() as conn:
        conn.execute("""INSERT INTO jobs_history(manager_job_id,node_id,node_job_id,drive,state,title,media_type,output_dir,barcode,updated_at)
                        VALUES (?,?,?,'DVD','complete',?,?,?,?,1)""",
                     (job_id, 'node', job_id, title, kind, str(folder), barcode))


def test_unified_library_includes_untransferred_rips_and_filters(tmp_path, monkeypatch):
    db = fresh(tmp_path, monkeypatch)
    import library
    add_rip(db, 'running', 'Running Man', 'movie', '/mnt/ripping/Running Man', '12345678')
    add_rip(db, 'show', 'Running Show', 'tv', '/mnt/ripping/Running Show')
    assert [x['title'] for x in library.search('Running', media_type='movie')] == ['Running Man']
    assert [x['title'] for x in library.search(media_type='tv')] == ['Running Show']
    assert library.search('12345678')[0]['location_status'] == 'Awaiting transfer'
    assert library.search('12345678', media_type='tv') == []
    assert library.get_item('rip:running')['title'] == 'Running Man'


def test_manifest_and_physical_record_join_without_title_deduplication(tmp_path, monkeypatch):
    db = fresh(tmp_path, monkeypatch)
    import library
    folder = tmp_path / 'media' / 'Movies' / 'Film'
    folder.mkdir(parents=True)
    db.set_settings({'mover_destination_root': str(tmp_path / 'media'),
                     'mover_destination_folders': json.dumps({'movie': 'Movies'})})
    add_rip(db, 'film', 'Film', 'movie', folder)
    add_rip(db, 'other', 'Film', 'movie', '/mnt/ripping/Other Edition')
    with db.write() as conn:
        conn.execute("INSERT INTO physical_media(manager_job_id,final_dir,created_at,updated_at) VALUES ('film',?,1,1)", (str(folder),))
    (folder / 'disc-info.json').write_text(json.dumps({'title': 'Film', 'media_type': 'movie', 'manager_job_id': 'film'}))
    rows = library.search()
    assert len(rows) == 2
    merged = next(x for x in rows if x['ownership'] == 'manifest')
    assert merged['manager_job_id'] == 'film'
    assert library.get_item(merged['id'])['manifest']['title'] == 'Film'


def test_local_nfo_and_artwork_are_separate_from_disc_photos(tmp_path, monkeypatch):
    db = fresh(tmp_path, monkeypatch)
    import library
    folder = tmp_path / 'media' / 'Movies' / 'Film'
    folder.mkdir(parents=True)
    db.set_settings({'mover_destination_root': str(tmp_path / 'media'),
                     'mover_destination_folders': json.dumps({'movie': 'Movies'})})
    (folder / 'disc-info.json').write_text(json.dumps({'title': 'Film'}))
    (folder / 'movie.nfo').write_text('<movie><title>Matched title</title><plot>Synopsis</plot><genre>Action</genre></movie>')
    (folder / 'poster.jpg').write_bytes(b'poster')
    item = library.search()[0]
    assert item['local_metadata']['overview'] == 'Synopsis'
    assert item['local_metadata']['genres'] == ['Action']
    assert item['artwork_url'].endswith('/artwork')
    assert not item.get('front_url')
    assert library.artwork_path(item) == folder / 'poster.jpg'
    (folder / 'poster.jpg').unlink()
    (folder / 'poster.jpg').symlink_to(tmp_path / 'outside.jpg')
    (tmp_path / 'outside.jpg').write_bytes(b'private')
    assert library.artwork_path(item) is None


def test_manifest_only_entry_can_open_photo_editor_without_duplicate(tmp_path, monkeypatch):
    db = fresh(tmp_path, monkeypatch)
    import library
    folder = tmp_path / 'media' / 'Movies' / 'Film'
    folder.mkdir(parents=True)
    db.set_settings({'mover_destination_root': str(tmp_path / 'media'),
                     'mover_destination_folders': json.dumps({'movie': 'Movies'})})
    manifest = folder / 'disc-info.json'
    original = json.dumps({'title': 'Film', 'year': 1999})
    manifest.write_text(original)
    item = library.search()[0]
    assert not item.get('manager_job_id')
    attached = library.ensure_physical(item['id'])
    assert attached['manager_job_id'].startswith('existing:')
    assert attached['year'] == 1999
    assert library.ensure_physical(item['id'])['manager_job_id'] == attached['manager_job_id']
    assert len(library.search()) == 1
    assert manifest.read_text() == original
