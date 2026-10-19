import json
from pathlib import Path
import pytest
from test_reliability import fresh_database, configure

def stage(tmp_path, monkeypatch, media_type="movie", name="Example"):
    db=fresh_database(tmp_path,monkeypatch)
    node=tmp_path/"unused-node";node.mkdir()
    media=configure(db,tmp_path,node)
    import media_intake
    folder=media_intake.folder_for(media_type,name);folder.mkdir(parents=True)
    ext={"movie":".mkv","tv":".mkv","music":".flac","audiobook":".m4b"}[media_type]
    (folder/("media"+ext)).write_bytes(b"media")
    return db,media,media_intake,folder

def test_queue_one_intake_item_does_not_queue_another(tmp_path,monkeypatch):
    db,media,intake,one=stage(tmp_path,monkeypatch,name="One")
    two=intake.folder_for("movie","Two");two.mkdir();(two/"two.mkv").write_bytes(b"two")
    items=intake.scan()["items"];target=next(x for x in items if x["name"]=="One")
    result=intake.queue(target["id"])
    assert result["queued"] is True
    assert len(db.query("SELECT * FROM transfer_queue"))==1
    assert not (two/"disc-info.json").exists()

def test_intake_collision_is_needs_attention_and_not_queued(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch,name="Collision")
    destination=media/"1 ~ Movies"/"Collision";destination.mkdir(parents=True)
    item=next(x for x in intake.scan()["items"] if x["name"]=="Collision")
    assert item["needs_attention"] is True
    with pytest.raises(FileExistsError):intake.queue(item["id"])
    assert not db.query("SELECT * FROM transfer_queue")

def test_loose_file_is_only_moved_by_explicit_queue(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch);node=tmp_path/"node";node.mkdir();media=configure(db,tmp_path,node)
    import media_intake
    section=media_intake.ensure()/"Movies";(section/"Loose Film.mkv").write_bytes(b"film")
    data=media_intake.scan()
    item=next(x for x in data["items"] if x["name"].startswith("Loose Film"))
    assert Path(item["folder"]).is_file()
    assert item["loose"] is True
    media_intake.queue(item["id"])
    assert not Path(item["folder"]).exists()
    prepared=media_intake.scan()["items"][0]
    assert Path(prepared["folder"]).is_dir()
    assert list(Path(prepared["folder"]).glob("*.mkv"))

def test_manifest_records_digital_import_provenance(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    item=intake.scan()["items"][0];intake.prepare(item["id"],provenance="digital_import",source="friend_collection")
    manifest=json.loads((folder/"disc-info.json").read_text())
    assert manifest["legacy_import"]["source"]=="digital_import"
    assert manifest["legacy_import"]["original_source"]=="friend_collection"

def test_content_identity_changes_when_same_folder_is_reused(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    first=intake.queue(intake.scan()["items"][0]["id"])["manager_job_id"]
    # Simulate completed intake being removed and a later, different collection using same name.
    import shutil
    shutil.rmtree(folder);folder.mkdir();(folder/"different.mkv").write_bytes(b"different content")
    second=intake.queue(intake.scan()["items"][0]["id"])["manager_job_id"]
    assert first!=second


def test_disabled_mover_adoption_can_be_queued_later(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    item=intake.scan()["items"][0]
    db.set_setting("mover_enabled",0)
    with pytest.raises(ValueError,match="disabled"):
        intake.queue(item["id"])
    assert len(db.query("SELECT * FROM jobs_history"))==1
    assert not db.query("SELECT * FROM transfer_queue")
    db.set_setting("mover_enabled",1)
    result=intake.queue(item["id"])
    assert result["queued"]
    assert len(db.query("SELECT * FROM transfer_queue"))==1


def test_scan_does_not_create_missing_directories(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    db.set_setting("mover_destination_root",str(tmp_path/"missing"))
    import media_intake
    assert media_intake.scan()["count"]==0
    assert not (tmp_path/"missing").exists()


def test_intake_identity_ignores_mtime_and_tracks_same_size_changes(tmp_path,monkeypatch):
    import os
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    item=intake.scan()["items"][0]
    first=intake.queue(item["id"])["manager_job_id"]
    f=folder/"media.mkv"
    os.utime(f,(1234,1234))
    assert intake.queue(item["id"])["manager_job_id"]==first
    f.write_bytes(b"other")
    os.utime(f,(1234,1234))
    assert intake.queue(item["id"])["manager_job_id"]!=first


def test_direct_folder_queue_does_not_scan_other_intake(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    monkeypatch.setattr(intake,"scan",lambda: (_ for _ in ()).throw(AssertionError("global scan")))
    assert intake.queue_folder(folder,"movie")["queued"]


def test_failed_transfer_explicit_queue_retries_existing_job(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    first=intake.queue_folder(folder,"movie")
    with db.write() as con:
        con.execute("UPDATE transfer_queue SET state='failed',error='offline'")
    retry=intake.queue_folder(folder,"movie")
    assert retry["retried"]
    assert retry["manager_job_id"]==first["manager_job_id"]
    assert db.query_one("SELECT state FROM transfer_queue")["state"]=="queued"


def test_identical_later_import_gets_new_transfer_and_preserves_old_history(tmp_path,monkeypatch):
    import mover,jobs
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    first=intake.queue_folder(folder,'movie')
    transfer=dict(db.query_one('SELECT * FROM transfer_queue'))
    job=jobs.row_to_dict(db.query_one('SELECT * FROM jobs_history WHERE manager_job_id=?',(first['manager_job_id'],)))
    mover._copy_transfer(transfer,job)
    assert not folder.exists()
    # Owner relocates the previous destination before staging the same bytes.
    destination=media/'1 ~ Movies'/folder.name
    destination.rename(media/'previous-import')
    folder.mkdir();(folder/'media.mkv').write_bytes(b'media')
    second=intake.queue_folder(folder,'movie')
    assert second['queued'] and second['manager_job_id']!=first['manager_job_id']
    assert intake.queue_folder(folder,'movie')['manager_job_id']==second['manager_job_id']
    assert len(db.query('SELECT * FROM transfer_queue'))==2
    assert db.query_one('SELECT state FROM transfer_queue WHERE manager_job_id=?',(first['manager_job_id'],))['state']=='complete'
    assert (media/'previous-import'/'media.mkv').read_bytes()==b'media'
    assert (folder/'media.mkv').read_bytes()==b'media'


def test_completed_adoption_marker_is_renewed_when_destination_is_missing(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    first=intake.queue_folder(folder,'movie')
    with db.write() as con: con.execute("UPDATE transfer_queue SET state='complete'")
    second=intake.queue_folder(folder,'movie')
    assert second['queued'] and second['manager_job_id']!=first['manager_job_id']
    assert intake.queue_folder(folder,'movie')['manager_job_id']==second['manager_job_id']


def test_intake_adoption_generation_survives_reload_and_sidecar_refresh(tmp_path,monkeypatch):
    import importlib,archive
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    first=intake.queue_folder(folder,'movie')
    archive.sync_sidecars(first['manager_job_id'],folder)
    importlib.reload(intake)
    second=intake.queue_folder(folder,'movie')
    assert second['manager_job_id']==first['manager_job_id']
    assert len(db.query('SELECT * FROM transfer_queue'))==1


def test_intake_migrates_failed_legacy_content_identity_without_duplicate(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    intake.prepare(intake.scan()['items'][0]['id'])
    identity=intake._content_fingerprint(folder,'movie')
    legacy='intake:'+identity
    with db.write() as con:
        con.execute("INSERT INTO jobs_history(manager_job_id,node_id,node_job_id,drive,state,title,media_type,output_dir,updated_at) VALUES (?,'Media-Intake',?,'INTAKE','complete','Example','movie',?,1)",(legacy,identity,str(folder)))
        con.execute("INSERT INTO transfer_queue(manager_job_id,state,created_at,updated_at) VALUES (?,'failed',1,1)",(legacy,))
    result=intake.queue_folder(folder,'movie')
    assert result['manager_job_id']==legacy and result['retried']
    assert len(db.query('SELECT * FROM jobs_history'))==1
