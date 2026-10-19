import json
from pathlib import Path
from test_reliability import fresh_database, configure
import library_maintenance as maintenance


def test_health_route_is_not_captured_by_generic_item_route(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    configure(db,tmp_path,tmp_path/"node")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.library import router
    app=FastAPI();app.include_router(router)
    response=TestClient(app).get("/api/collection/health-scan")
    assert response.status_code==200
    assert response.json()["ok"]


def test_repair_backs_up_invalid_json_and_never_alters_media(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    media=configure(db,tmp_path,tmp_path/"node")
    folder=media/"1 ~ Movies"/"Film";folder.mkdir(parents=True)
    media_file=folder/"film.mkv";media_file.write_bytes(b"valuable")
    target=folder/"disc-info.json";target.write_bytes(b'{damaged user notes\xff')
    result=maintenance.repair()
    assert result["manifests_repaired"]==1
    payload=json.loads(target.read_text())
    assert (folder/payload["repair"]["original_backup"]).read_bytes()==b'{damaged user notes\xff'
    assert media_file.read_bytes()==b"valuable"
    assert maintenance.repair()["manifests_repaired"]==0


def test_repair_queues_only_safe_stranded_rips(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    node=tmp_path/"node";media=configure(db,tmp_path,node)
    for name in ("Ready","Collision"):
        folder=node/"Movies"/name;folder.mkdir(parents=True);(folder/"movie.mkv").write_bytes(b"media")
    (media/"1 ~ Movies"/"Collision").mkdir(parents=True)
    result=maintenance.repair()
    assert result["stranded_queued"]==1
    assert len(result["needs_attention"])==1
    assert len(db.query("SELECT * FROM transfer_queue"))==1
    assert (node/"Movies"/"Ready"/"movie.mkv").exists()
    assert (node/"Movies"/"Collision"/"movie.mkv").exists()


def test_background_job_reports_progress_without_blocking_caller(tmp_path,monkeypatch):
    import threading
    db=fresh_database(tmp_path,monkeypatch)
    entered=threading.Event();release=threading.Event()
    def index(progress):
        progress(1,2,"one of two");entered.set()
        assert release.wait(5)
        return {"database_updated":2}
    monkeypatch.setattr(maintenance.library,"check_manifests",index)
    job=maintenance.start("index")
    try:
        assert job["status"]=="running"
        assert entered.wait(5)
        status=maintenance.status()
        assert status["done"]==1 and status["total"]==2
        assert maintenance.start("index")["id"]==job["id"]
    finally:
        release.set();maintenance._future.result(timeout=5)
    assert maintenance.status()["status"]=="complete"
    assert maintenance.status()["result"]["database_updated"]==2


def test_index_keeps_records_on_unavailable_mount(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    media=configure(db,tmp_path,tmp_path/"node")
    folder=media/"4 ~ Audiobooks"/"Book";folder.mkdir(parents=True)
    (folder/"disc-info.json").write_text('{"title":"Book"}')
    import library
    library.check_manifests()
    media.rename(tmp_path/"disconnected")
    library.check_manifests()
    assert db.query_one("SELECT title FROM library_items")["title"]=="Book"
