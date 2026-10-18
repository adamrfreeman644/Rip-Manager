import json
import time
from pathlib import Path
import pytest


def fresh_database(tmp_path, monkeypatch):
    import db, archive
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rip-manager.db")
    monkeypatch.setattr(archive, "PHYSICAL_MEDIA_DIR", tmp_path / "physical-media")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
    db._local.conn = None
    db._settings_cache.clear()
    db.init_db()
    return db


def configure(db, tmp_path, node_mount):
    media = tmp_path / "media"
    db.set_settings({
        "mover_enabled": 1,
        "mover_delete_source": 1,
        "mover_node_mounts": json.dumps({"node-1": str(node_mount)}),
        "mover_node_source_roots": json.dumps({"node-1": "/mnt/ripping"}),
        "mover_destination_root": str(media),
        "mover_destination_folders": json.dumps({
            "movie": "1 ~ Movies", "tv": "2 ~ TV",
            "music": "3 ~ Music", "audiobook": "4 ~ Audiobooks",
        }),
    })
    return media


@pytest.mark.parametrize("kind,root,filename", [
    ("movie", "Movies", "film.mkv"),
    ("tv", "TV", "Season 01/episode.mkv"),
    ("music", "Music", "Disc 1/track.flac"),
    ("audiobook", "AudioBooks", "Disc 1/chapter.mp3"),
])
def test_recovery_discovers_all_media_and_queues_standard_mover(tmp_path, monkeypatch, kind, root, filename):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    folder = node / root / ("Example " + kind)
    media_file = folder / filename
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"valuable media")
    (folder / "cover.jpg").write_bytes(b"art")
    configure(db, tmp_path, node)
    import recovery
    rows = recovery.discover()
    row = next(x for x in rows if x["media_type"] == kind)
    assert row["folder"] == folder.name
    result = recovery.adopt("node-1", folder.name, kind)
    assert result["queued"] is True
    queue = db.query_one("SELECT state FROM transfer_queue WHERE manager_job_id=?", (result["manager_job_id"],))
    assert queue["state"] == "queued"
    assert media_file.exists()
    assert (folder / "cover.jpg").exists()
    assert (folder / "disc-info.json").is_file()


def test_hash_failure_never_deletes_source(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    source = node / "Movies" / "Precious Film"
    source.mkdir(parents=True)
    (source / "movie.mkv").write_bytes(b"irreplaceable")
    media = configure(db, tmp_path, node)
    import recovery, mover, jobs
    result = recovery.adopt("node-1", source.name, "movie")
    row = db.query_one("SELECT * FROM jobs_history WHERE manager_job_id=?", (result["manager_job_id"],))
    job = jobs.row_to_dict(row)
    transfer = db.query_one("SELECT * FROM transfer_queue WHERE manager_job_id=?", (result["manager_job_id"],))
    real_hash = mover._hash
    monkeypatch.setattr(mover, "_hash", lambda path: "forced-mismatch" if ".partial" in str(path) else real_hash(path))
    with pytest.raises(IOError, match="Verification failed"):
        mover._copy_transfer(dict(transfer), job)
    assert source.is_dir()
    assert (source / "movie.mkv").read_bytes() == b"irreplaceable"
    assert not (media / "1 ~ Movies" / source.name).exists()


def test_recovery_transfer_preserves_complete_nested_folder(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    source = node / "TV" / "Example Show"
    (source / "Season 01").mkdir(parents=True)
    (source / "Season 01" / "episode.mkv").write_bytes(b"episode")
    (source / "Season 01" / "episode.en.srt").write_text("subtitle")
    (source / "artwork").mkdir()
    (source / "artwork" / "poster.jpg").write_bytes(b"poster")
    media = configure(db, tmp_path, node)
    import recovery, mover, jobs
    result = recovery.adopt("node-1", source.name, "tv")
    job = jobs.row_to_dict(db.query_one("SELECT * FROM jobs_history WHERE manager_job_id=?", (result["manager_job_id"],)))
    transfer = dict(db.query_one("SELECT * FROM transfer_queue WHERE manager_job_id=?", (result["manager_job_id"],)))
    mover._copy_transfer(transfer, job)
    destination = media / "2 ~ TV" / source.name
    assert not source.exists()
    assert (destination / "Season 01" / "episode.mkv").read_bytes() == b"episode"
    assert (destination / "Season 01" / "episode.en.srt").read_text() == "subtitle"
    assert (destination / "artwork" / "poster.jpg").read_bytes() == b"poster"
    assert (destination / "disc-info.json").is_file()


def test_destination_collision_keeps_recovery_source(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    source = node / "Movies" / "Collision"
    source.mkdir(parents=True)
    (source / "movie.mkv").write_bytes(b"source")
    media = configure(db, tmp_path, node)
    destination = media / "1 ~ Movies" / "Collision"
    destination.mkdir(parents=True)
    (destination / "movie.mkv").write_bytes(b"existing")
    import recovery
    with pytest.raises(FileExistsError):
        recovery.adopt("node-1", source.name, "movie")
    assert (source / "movie.mkv").read_bytes() == b"source"
    assert (destination / "movie.mkv").read_bytes() == b"existing"


def test_interrupted_transfer_is_requeued_not_deleted(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    now = time.time()
    with db.write() as conn:
        conn.execute("""INSERT INTO jobs_history(manager_job_id,node_id,node_job_id,drive,state,title,media_type,output_dir,updated_at)
                        VALUES ('j','node-1','j','DVD','complete','Film','movie','/mnt/ripping/Movies/Film',?)""",(now,))
        conn.execute("INSERT INTO transfer_queue(manager_job_id,state,created_at,updated_at) VALUES ('j','copying',?,?)",(now,now))
    import mover, asyncio
    async def exercise():
        mover.start()
        await asyncio.sleep(0)
        await mover.stop()
    # Prevent worker from consuming the requeued item; validate restart reset directly.
    monkeypatch.setattr(mover, "_loop", lambda: asyncio.sleep(3600))
    asyncio.run(exercise())
    row=db.query_one("SELECT state,error FROM transfer_queue WHERE manager_job_id='j'")
    assert row["state"] == "queued"
    assert "restarted" in row["error"].lower()
