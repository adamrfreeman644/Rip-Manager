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

def test_loose_file_becomes_safe_intake_unit(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch);node=tmp_path/"node";node.mkdir();media=configure(db,tmp_path,node)
    import media_intake
    section=media_intake.ensure()/"Movies";(section/"Loose Film.mkv").write_bytes(b"film")
    data=media_intake.scan()
    item=next(x for x in data["items"] if x["name"].startswith("Loose Film"))
    assert Path(item["folder"]).is_dir()
    assert list(Path(item["folder"]).glob("*.mkv"))

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
