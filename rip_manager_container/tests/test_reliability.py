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


@pytest.mark.parametrize("kind", ["movie", "tv", "music", "audiobook"])
def test_rip_node_source_manifest_exists_for_every_media_type(tmp_path, kind):
    from test_node_verification import load_node
    node = load_node()
    output = tmp_path / kind / "Example"
    request = node.RipRequest(title="Example", media_type=kind)
    job = node.Job("DVD1", output, ["true"], request)
    node.write_source_manifest(job)
    manifest = json.loads((output / "disc-info.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["media_type"] == kind
    assert manifest["rip"]["state"] == "starting"
    assert manifest["title"] == "Example"


def test_invalid_existing_source_manifest_is_never_overwritten(tmp_path):
    from test_node_verification import load_node
    node = load_node()
    output = tmp_path / "movie" / "Example"
    output.mkdir(parents=True)
    target = output / "disc-info.json"
    target.write_text("{damaged", encoding="utf-8")
    job = node.Job("DVD1", output, ["true"], node.RipRequest(title="Example", media_type="movie"))
    node.write_source_manifest(job)
    assert target.read_text(encoding="utf-8") == "{damaged"


def test_app_health_route_starts(tmp_path, monkeypatch):
    fresh_database(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["ok"] is True


def test_recovery_same_path_new_generation_gets_new_job(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    import recovery
    mount = tmp_path / "node"
    configure(db, tmp_path, mount)
    source = mount / "Movies" / "Reusable Movie"
    source.mkdir(parents=True)
    first_file = source / "movie.mkv"
    first_file.write_bytes(b"first generation")
    first = recovery.adopt("node-1", "Reusable Movie", "movie")
    first_id = first["manager_job_id"]

    # Simulate the completed transfer having removed the source, then a later rip
    # reusing exactly the same node/media/folder path with different content.
    import shutil
    shutil.rmtree(source)
    source.mkdir(parents=True)
    second_file = source / "movie.mkv"
    second_file.write_bytes(b"a genuinely different second generation")
    second = recovery.adopt("node-1", "Reusable Movie", "movie")

    assert second["manager_job_id"] != first_id
    rows = db.query("SELECT manager_job_id FROM jobs_history WHERE drive='RECOVERY' ORDER BY manager_job_id")
    ids = {row["manager_job_id"] for row in rows}
    assert first_id in ids
    assert second["manager_job_id"] in ids


def test_recovery_repeated_adoption_keeps_job_identity(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    import recovery
    mount = tmp_path / "node"
    configure(db, tmp_path, mount)
    source = mount / "Movies" / "Film"
    source.mkdir(parents=True)
    (source / "movie.mkv").write_bytes(b"media")
    first = recovery.adopt("node-1", source.name)
    second = recovery.adopt("node-1", source.name)
    assert first["manager_job_id"] == second["manager_job_id"]
    assert len(db.query("SELECT * FROM transfer_queue")) == 1


def test_recovery_identical_files_in_different_folders_get_distinct_jobs(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    import recovery, os
    mount = tmp_path / "node"
    configure(db, tmp_path, mount)
    ids = []
    for name in ("Film A", "Film B"):
        source = mount / "Movies" / name
        source.mkdir(parents=True)
        media = source / "movie.mkv"
        media.write_bytes(b"same media")
        os.utime(media, ns=(1_700_000_000_000_000_000,) * 2)
        ids.append(recovery.adopt("node-1", name)["manager_job_id"])
    assert len(set(ids)) == 2
    assert len(db.query("SELECT * FROM transfer_queue")) == 2


@pytest.mark.parametrize("mutation", ["add", "replace", "remove", "corrupt_destination"])
def test_mover_detects_changes_after_a_file_was_copied(tmp_path, monkeypatch, mutation):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    source = node / "Movies" / "Changing Film"
    source.mkdir(parents=True)
    media_file = source / "movie.mkv"
    media_file.write_bytes(b"original")
    media = configure(db, tmp_path, node)
    import recovery, mover, jobs, os
    result = recovery.adopt("node-1", source.name)
    job = jobs.row_to_dict(db.query_one("SELECT * FROM jobs_history WHERE manager_job_id=?", (result["manager_job_id"],)))
    transfer = dict(db.query_one("SELECT * FROM transfer_queue WHERE manager_job_id=?", (result["manager_job_id"],)))
    real_hash = mover._hash
    changed = False

    def mutate_after_hash(path):
        nonlocal changed
        digest = real_hash(path)
        if path.name == "movie.mkv" and ".partial" in str(path) and not changed:
            changed = True
            if mutation == "add":
                (source / "new-extra.mkv").write_bytes(b"new extra")
            elif mutation == "replace":
                stat = media_file.stat()
                media_file.write_bytes(b"replaced")  # Same size and mtime must still be caught.
                os.utime(media_file, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            elif mutation == "remove":
                media_file.unlink()
            else:
                path.write_bytes(b"corrupt!")
        return digest

    monkeypatch.setattr(mover, "_hash", mutate_after_hash)
    with pytest.raises(IOError, match="changed|Verification failed"):
        mover._copy_transfer(transfer, job)
    assert changed
    assert source.is_dir()
    if mutation == "add":
        assert (source / "new-extra.mkv").read_bytes() == b"new extra"
    elif mutation != "remove":
        assert media_file.read_bytes() == (b"replaced" if mutation == "replace" else b"original")
    destination = media / "1 ~ Movies" / source.name
    assert not destination.exists()
    # A stable retry recreates the partial copy and verifies the new inventory.
    if mutation != "remove":
        monkeypatch.setattr(mover, "_hash", real_hash)
        mover._copy_transfer(transfer, job)
        assert not source.exists()
        assert (destination / "movie.mkv").read_bytes() == (b"replaced" if mutation == "replace" else b"original")
        if mutation == "add":
            assert (destination / "new-extra.mkv").read_bytes() == b"new extra"


def test_mover_retains_source_changed_during_archive_finalization(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    source = node / "Movies" / "Late Extra"
    source.mkdir(parents=True)
    (source / "movie.mkv").write_bytes(b"original")
    configure(db, tmp_path, node)
    import recovery, mover, jobs, archive
    result = recovery.adopt("node-1", source.name)
    job = jobs.row_to_dict(db.query_one("SELECT * FROM jobs_history WHERE manager_job_id=?", (result["manager_job_id"],)))
    transfer = dict(db.query_one("SELECT * FROM transfer_queue WHERE manager_job_id=?", (result["manager_job_id"],)))
    real_final = archive.set_final_dir

    def add_late_extra(*args):
        real_final(*args)
        (source / "late-extra.mkv").write_bytes(b"retain me")

    monkeypatch.setattr(archive, "set_final_dir", add_late_extra)
    mover._copy_transfer(transfer, job)
    assert (source / "late-extra.mkv").read_bytes() == b"retain me"
    assert (source / "movie.mkv").read_bytes() == b"original"
    row = db.query_one("SELECT state,error FROM transfer_queue WHERE id=?", (transfer["id"],))
    assert row["state"] == "complete"
    assert "source retained" in row["error"].lower()


@pytest.mark.parametrize("prefix", ["", "Rips"])
def test_recovery_supports_both_audiobook_roots_together(tmp_path, monkeypatch, prefix):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    configure(db, tmp_path, node)
    for spelling in ("AudioBooks", "Audiobooks"):
        book = node / prefix / spelling / "Book"
        book.mkdir(parents=True)
        (book / "chapter.m4b").write_bytes(spelling.encode())
    import recovery
    rows = recovery.discover()
    assert len(rows) == 2
    assert len({row["folder"] for row in rows}) == 2
    for row in rows:
        assert recovery.adopt("node-1", row["folder"], "audiobook")["queued"]


@pytest.mark.parametrize("kind,root,extension", [("music", "Music", ".flac"), ("audiobook", "Audiobooks", ".m4b")])
def test_recovery_splits_artist_author_containers_and_keeps_discs(tmp_path, monkeypatch, kind, root, extension):
    db = fresh_database(tmp_path, monkeypatch)
    node = tmp_path / "node"
    configure(db, tmp_path, node)
    for work in ("Work A", "Work B"):
        for disc in ("Disc 1", "Disc 2"):
            folder = node / root / "Creator" / work / disc
            folder.mkdir(parents=True)
            (folder / ("track" + extension)).write_bytes(b"media")
    import recovery
    rows = recovery.discover()
    assert len(rows) == 2
    for row in rows:
        result = recovery.adopt("node-1", row["folder"], kind)
        assert result["queued"]
        assert {x["path"] for x in result["files"]} == {"Disc 1/track" + extension, "Disc 2/track" + extension}
    with pytest.raises(ValueError):
        recovery.adopt("node-1", "Creator", kind)
