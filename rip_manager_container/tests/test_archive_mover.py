import json
import time
from pathlib import Path


def fresh_database(tmp_path, monkeypatch):
    import db
    import archive
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rip-manager.db")
    monkeypatch.setattr(archive, "PHYSICAL_MEDIA_DIR", tmp_path / "physical-media")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
    db._local.conn = None
    db._settings_cache.clear()
    db.init_db()
    return db


def add_complete_job(db, output_dir):
    now = time.time()
    with db.write() as conn:
        conn.execute(
            """INSERT INTO jobs_history(
              manager_job_id,node_id,node_job_id,drive,state,title,year,barcode,media_type,
              started_at,finished_at,progress,last_message,output_dir,verification_json,raw_json,
              auto_started,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("rip-node-1:job-1", "rip-node-1", "job-1", "DVD1", "complete", "Test Film",
             2026, "123456", "movie", now-10, now, 100, "Complete", output_dir,
             json.dumps({"ok": True, "files_checked": 1}), json.dumps({"output_bytes": 4}), 0, now),
        )


def test_user_text_survives_generated_refresh(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    add_complete_job(db, "/mnt/ripping/Movies/Test Film")
    import archive
    archive.save_text("rip-node-1:job-1", "My exact handwritten note\n")
    first = archive.text_for("rip-node-1:job-1")
    assert "My exact handwritten note" in first
    assert archive.AUTO_START in first
    with db.write() as conn:
        conn.execute("UPDATE jobs_history SET last_message='Transcoded' WHERE manager_job_id='rip-node-1:job-1'")
    refreshed = archive.text_for("rip-node-1:job-1")
    assert "My exact handwritten note" in refreshed
    assert refreshed.count(archive.AUTO_START) == 1


def test_mover_maps_only_beneath_configured_mount(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node_mount = tmp_path / "node"
    media = tmp_path / "media"
    source = node_mount / "Movies" / "Test Film"
    source.mkdir(parents=True)
    (source / "movie.mkv").write_bytes(b"test")
    add_complete_job(db, "/mnt/ripping/Movies/Test Film")
    db.set_settings({
        "mover_enabled": 1,
        "mover_node_mounts": json.dumps({"rip-node-1": str(node_mount)}),
        "mover_node_source_roots": json.dumps({"rip-node-1": "/mnt/ripping"}),
        "mover_destination_root": str(media),
        "mover_destination_folders": json.dumps({"movie": "Movies", "tv": "TV", "music": "Music", "audiobook": "Audiobooks"}),
    })
    import jobs
    import mover
    job = jobs.row_to_dict(db.query_one("SELECT * FROM jobs_history WHERE manager_job_id='rip-node-1:job-1'"))
    mapped_source, destination = mover._resolve_paths(job)
    assert mapped_source == source.resolve()
    assert destination == (media / "Movies" / "Test Film").resolve()


def test_media_list_marks_history_without_an_existing_folder(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node_mount = tmp_path / "node"
    existing = node_mount / "Movies" / "Still Here"
    existing.mkdir(parents=True)
    add_complete_job(db, "/mnt/ripping/Movies/Missing")
    with db.write() as conn:
        conn.execute("UPDATE jobs_history SET manager_job_id='rip-node-1:missing',node_job_id='missing'")
    add_complete_job(db, "/mnt/ripping/Movies/Still Here")
    db.set_settings({
        "mover_node_mounts": json.dumps({"rip-node-1": str(node_mount)}),
        "mover_node_source_roots": json.dumps({"rip-node-1": "/mnt/ripping"}),
    })
    import archive
    rows = archive.list_media()
    assert [row["manager_job_id"] for row in rows] == ["rip-node-1:job-1", "rip-node-1:missing"]
    assert rows[0]["existing_dir"] == str(existing.resolve())
    assert rows[0]["storage_available"] is True
    assert rows[1]["storage_available"] is False
    assert rows[1]["expected_dir"] == str(node_mount / "Movies" / "Missing")


def test_failed_copy_keeps_source(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node_mount = tmp_path / "node"
    media = tmp_path / "media"
    source = node_mount / "Movies" / "Test Film"
    source.mkdir(parents=True)
    (source / "movie.mkv").write_bytes(b"test")
    destination = media / "Movies" / "Test Film"
    destination.mkdir(parents=True)
    add_complete_job(db, "/mnt/ripping/Movies/Test Film")
    db.set_settings({
        "mover_enabled": 1,
        "mover_node_mounts": json.dumps({"rip-node-1": str(node_mount)}),
        "mover_node_source_roots": json.dumps({"rip-node-1": "/mnt/ripping"}),
        "mover_destination_root": str(media),
        "mover_destination_folders": json.dumps({"movie": "Movies", "tv": "TV", "music": "Music", "audiobook": "Audiobooks"}),
    })
    import jobs
    import mover
    job = jobs.row_to_dict(db.query_one("SELECT * FROM jobs_history WHERE manager_job_id='rip-node-1:job-1'"))
    transfer = {"id": 99}
    try:
        mover._copy_transfer(transfer, job)
    except FileExistsError:
        pass
    assert source.is_dir()
    assert (source / "movie.mkv").read_bytes() == b"test"


def test_verified_copy_writes_sidecars_then_removes_source(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    node_mount = tmp_path / "node"
    media = tmp_path / "media"
    source = node_mount / "Movies" / "Test Film"
    source.mkdir(parents=True)
    (source / "movie.mkv").write_bytes(b"verified media")
    add_complete_job(db, "/mnt/ripping/Movies/Test Film")
    db.set_settings({
        "mover_enabled": 1,
        "mover_delete_source": 1,
        "mover_node_mounts": json.dumps({"rip-node-1": str(node_mount)}),
        "mover_node_source_roots": json.dumps({"rip-node-1": "/mnt/ripping"}),
        "mover_destination_root": str(media),
        "mover_destination_folders": json.dumps({"movie": "Movies", "tv": "TV", "music": "Music", "audiobook": "Audiobooks"}),
    })
    import jobs
    import mover
    job = jobs.row_to_dict(db.query_one("SELECT * FROM jobs_history WHERE manager_job_id='rip-node-1:job-1'"))
    with db.write() as conn:
        transfer_id = conn.execute(
            "INSERT INTO transfer_queue(manager_job_id,state,created_at,updated_at) VALUES (?,'queued',?,?)",
            (job["manager_job_id"], time.time(), time.time()),
        ).lastrowid
    mover._copy_transfer({"id": transfer_id}, job)
    destination = media / "Movies" / "Test Film"
    assert not source.exists()
    assert (destination / "Test Film.mkv").read_bytes() == b"verified media"
    assert (destination / "disc-info.txt").is_file()
    manifest = json.loads((destination / "disc-info.json").read_text(encoding="utf-8"))
    transfer_log = [entry for entry in manifest["change_log"] if any(
        change.get("action") == "moved_and_verified" for change in entry["changes"]
    )]
    assert transfer_log
    moved = next(change for change in transfer_log[-1]["changes"] if change.get("action") == "moved_and_verified")
    assert moved["path"] == "Test Film.mkv"
    assert moved["size_bytes"] == len(b"verified media")
    assert moved["extension"] == ".mkv"
    assert moved["old_paths"] == [str(source / "Test Film.mkv")]
    assert moved["current_path"] == "Test Film.mkv"
    assert any(event["event"] == "transfer_finished" for event in manifest["process_history"])
    file_entry = next(item for item in manifest["files"] if item["path"] == "Test Film.mkv")
    assert any(event["event"] == "transfer_finished" for event in file_entry["process_history"])
    transfer = db.query_one("SELECT state,error FROM transfer_queue WHERE id=?", (transfer_id,))
    assert transfer["state"] == "complete"
    assert transfer["error"] is None


def test_scan_existing_writes_json_only_and_is_idempotent(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    media = tmp_path / "media"
    for relative, filename in (
        ("1 ~ Movies/Film", "film.mkv"),
        ("2 ~ TV/Show/Season 1", "episode.mkv"),
        ("3 ~ Music/Artist/Album", "track.flac"),
        ("4 ~ Audiobooks/Book/Disc 1", "chapter.mp3"),
    ):
        folder = media / relative
        folder.mkdir(parents=True)
        (folder / filename).write_bytes(b"media")
    db.set_settings({
        "mover_destination_root": str(media),
        "mover_destination_folders": json.dumps({"movie": "Movies", "tv": "TV", "music": "Music", "audiobook": "Audiobooks"}),
        "mover_destination_folders": json.dumps({
            "movie": "1 ~ Movies", "tv": "2 ~ TV",
            "music": "3 ~ Music", "audiobook": "4 ~ Audiobooks",
        }),
    })
    import archive
    first = archive.scan_existing()
    assert first["folders_seen"] == first["records_processed"] == first["imported"] == 4
    assert first["manifests_created"] == 4
    manifests = list(media.rglob("disc-info.json"))
    assert len(manifests) == 4
    assert not list(media.rglob("disc-info.txt"))
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert payload["legacy_import"]["imported"] is True
    assert payload["rip"]["started_at"] is None
    assert payload["files"][0]["size_bytes"] == len(b"media")

    # The scan also reaches the already-imported database rows without duplicates.
    second = archive.scan_existing()
    assert second["imported"] == 0
    assert second["records_processed"] == 4
    assert second["manifests_unchanged"] == 4
    assert db.query_one("SELECT COUNT(*) AS count FROM jobs_history")["count"] == 4


def test_existing_manifest_keeps_richer_values(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    folder = tmp_path / "media" / "Movies" / "Film"
    folder.mkdir(parents=True)
    (folder / "film.mkv").write_bytes(b"media")
    existing = {"title": "Curated title", "rip": {"drive": "DVD9"}, "custom": {"keep": True}}
    (folder / "disc-info.json").write_text(json.dumps(existing), encoding="utf-8")
    db.set_settings({
        "mover_destination_root": str(tmp_path / "media"),
        "mover_destination_folders": json.dumps({
            "movie": "Movies", "tv": "TV", "music": "Music", "audiobook": "Audiobooks",
        }),
    })
    import archive
    result = archive.scan_existing()
    assert result["manifests_updated"] == 1
    payload = json.loads((folder / "disc-info.json").read_text(encoding="utf-8"))
    assert payload["title"] == "Curated title"
    assert payload["rip"]["drive"] == "DVD9"
    assert payload["custom"] == {"keep": True}



def test_physical_media_upc_updates_database_and_manifest(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    folder = tmp_path / "media" / "Movies" / "Test Film"
    folder.mkdir(parents=True)
    (folder / "movie.mkv").write_bytes(b"media")
    add_complete_job(db, str(folder))
    with db.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO physical_media(manager_job_id,final_dir,created_at,updated_at) VALUES (?,?,?,?)",
            ("rip-node-1:job-1", str(folder), time.time(), time.time()),
        )
    import archive
    archive.sync_sidecars("rip-node-1:job-1", folder)
    assert archive.save_barcode("rip-node-1:job-1", "5051892234567") == "5051892234567"
    assert db.query_one("SELECT barcode FROM jobs_history WHERE manager_job_id='rip-node-1:job-1'")["barcode"] == "5051892234567"
    payload = json.loads((folder / "disc-info.json").read_text(encoding="utf-8"))
    assert payload["upc"] == "5051892234567"
    archive.save_barcode("rip-node-1:job-1", None)
    payload = json.loads((folder / "disc-info.json").read_text(encoding="utf-8"))
    assert payload["upc"] is None


def test_media_prep_tv_keeps_provenance_and_sorts_extras(tmp_path, monkeypatch):
    import media_prep
    source=tmp_path/"Show"/"Season 1"/"Disk 2";source.mkdir(parents=True)
    files=[]
    for name in ("title00.mkv","title01.mkv","title02.mkv"):
        p=source/name;p.write_bytes(b"x");files.append(p)
    (source/"disc-info.txt").write_text("complete",encoding="utf-8")
    durations={files[0]:1800,files[1]:1780,files[2]:300}
    monkeypatch.setattr(media_prep,"_duration",lambda p:durations[p])
    result=media_prep.prepare_completed_rip(source,{"state":"complete","media_type":"tv","title":"Example","season":1,"disc":2})
    assert result["extras"]==1
    assert (source/"Example S01 D02F01.mkv").is_file()
    assert (source/"Example S01 D02F02.mkv").is_file()
    assert (source/"extras"/"Example S01 D02F03.mkv").is_file()

def test_media_prep_requires_completion_marker(tmp_path):
    import media_prep
    source=tmp_path/"rip";source.mkdir()
    (source/"title00.mkv").write_bytes(b"x")
    try:
        media_prep.prepare_completed_rip(source,{"state":"complete","media_type":"movie","title":"Film"})
        assert False,"expected marker gate"
    except ValueError as exc:
        assert "marker" in str(exc).lower()

def test_media_prep_skips_but_preserves_high_confidence_tv_play_all(tmp_path, monkeypatch):
    import media_prep
    source=tmp_path/"rip";source.mkdir()
    files=[]
    for name in ("playall.mkv","ep1.mkv","ep2.mkv"):
        p=source/name;p.write_bytes(b"x");files.append(p)
    (source/"disc-info.txt").write_text("complete",encoding="utf-8")
    durations={files[0]:3600,files[1]:1800,files[2]:1800}
    monkeypatch.setattr(media_prep,"_duration",lambda p:durations[p])
    result=media_prep.prepare_completed_rip(source,{"state":"complete","media_type":"tv","title":"Show","season":1,"disc":1})
    assert result["play_all_removed"]==1
    assert not files[0].exists()  # Removed from the episode sequence, never destroyed.
    assert result["play_all_preserved"]==1
    assert len(list(source.rglob("*.mkv")))==3
    assert (source/"extras"/"Show S01 D01 Play All.mkv").read_bytes()==b"x"


def test_tv_media_labels_include_series_without_rescanning(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    root = tmp_path / "media"
    db.set_settings({"mover_destination_root": str(root),
                     "mover_destination_folders": json.dumps({"tv": "2 ~ TV"})})
    import archive
    for leaf in ("Season 01", "Bloopers", "Season 01/extras"):
        folder = root / "2 ~ TV" / "Example Show" / leaf
        folder.mkdir(parents=True, exist_ok=True)
        assert archive.media_display_title({"media_type": "tv", "title": folder.name,
                                           "final_dir": str(folder)}) == "Example Show · " + leaf.replace("/", " · ")
    # Previously scanned rows keep their identities and photo associations.
    add_complete_job(db, str(root / "2 ~ TV" / "Example Show" / "Season 01"))
    with db.write() as conn:
        conn.execute("UPDATE jobs_history SET media_type='tv',title='Season 01',node_id='Byte-Me'")
    row = archive.list_media()[0]
    assert row["display_title"] == "Example Show · Season 01"
    assert row["title"] == "Season 01"
    assert row["manager_job_id"] == "rip-node-1:job-1"


def test_tv_labels_handle_unavailable_rips_and_leave_movies_unchanged(tmp_path, monkeypatch):
    fresh_database(tmp_path, monkeypatch)
    import archive
    assert archive.media_display_title({"media_type": "tv", "title": "Season 01",
        "output_dir": "/mnt/ripping/TV/Example/Season 01"}) == "Example · Season 01"
    assert archive.media_display_title({"media_type": "tv", "title": "Example", "season": 1,
        "disc": 2}) == "Example · Season 01 · Disc 2"
    assert archive.media_display_title({"media_type": "tv", "title": "Example S01 D02", "season": 1,
        "disc": 2}) == "Example S01 D02"
    assert archive.media_display_title({"media_type": "movie", "title": "Film",
        "final_dir": "/media/Movies/Film/extras"}) == "Film"


def test_retry_group_keeps_latest_success_and_photos_from_older_attempts():
    import archive
    def row(identity, state, **extra):
        return {"manager_job_id": identity, "title": "Film", "media_type": "movie",
                "expected_dir": "/rip-nodes/node/Movies/Film", "year": 2019,
                "state": state, "extra_urls": [], **extra}
    attempts = [row("latest-failed", "verification_failed"),
                row("success", "complete", rear_url="/success/rear"),
                row("old-failed", "verification_failed", front_url="/old/front", extra_urls=["/old/extra1", "/old/extra2"]),
                row("old-success", "complete", front_url="/success/front")]
    grouped = archive.group_media_attempts(attempts)
    assert len(grouped) == 1
    item = grouped[0]
    assert item["manager_job_id"] == "success"
    assert item["attempt_count"] == 4
    assert item["latest_attempt_id"] == "latest-failed"
    assert item["latest_attempt_state"] == "verification_failed"
    assert item["front_url"] == "/old/front"
    assert item["front_image_job_id"] == "old-failed"
    assert item["rear_image_job_id"] == "success"
    assert item["extra_sources"] == [{"manager_job_id": "old-failed", "index": 0},
                                     {"manager_job_id": "old-failed", "index": 1}]
    assert attempts[1]["extra_urls"] == []  # No mutation of original rip records.


def test_retry_group_keeps_distinct_folders_discs_and_unknown_paths():
    import archive
    rows = []
    for index, (folder, season, disc) in enumerate([
        ("/media/TV/Show/Season 01", 1, 1), ("/media/TV/Show/Season 01", 1, 2),
        ("/media/TV/Show/Season 02", 2, 1), ("/media/TV/Other/Season 01", 1, 1),
        (None, None, None), (None, None, None),
    ]):
        rows.append({"manager_job_id": str(index), "title": "Season 01", "media_type": "tv",
                     "expected_dir": folder, "season": season, "disc": disc, "state": "failed"})
    assert len(archive.group_media_attempts(rows)) == len(rows)
    repeated = [{"manager_job_id": "new", "media_type": "movie", "state": "failed", "node_id": "node-1", "output_dir": "/mnt/ripping/Film"},
                {"manager_job_id": "old", "media_type": "movie", "state": "verification_failed", "node_id": "node-1", "output_dir": "/mnt/ripping/Film"}]
    result = archive.group_media_attempts(repeated)
    assert len(result) == 1
    assert result[0]["manager_job_id"] == "new"
    other_node = {**repeated[0], "manager_job_id": "other-node", "node_id": "node-2"}
    assert len(archive.group_media_attempts([*repeated, other_node])) == 2


def test_media_list_groups_attempts_without_deleting_history_or_images(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    add_complete_job(db, "/mnt/ripping/Movies/Film")
    with db.write() as conn:
        conn.execute("UPDATE jobs_history SET manager_job_id='old',node_job_id='old',started_at=1,state='verification_failed'")
        conn.execute("INSERT INTO physical_media(manager_job_id,front_image,extras_json,created_at,updated_at) VALUES ('old','cover.jpg','[\"extra.jpg\"]',1,1)")
    add_complete_job(db, "/mnt/ripping/Movies/Film")
    import archive
    grouped = archive.list_media()
    assert len(grouped) == 1
    assert grouped[0]["attempt_count"] == 2
    assert grouped[0]["manager_job_id"] == "rip-node-1:job-1"
    assert grouped[0]["front_url"] == "/archive/old/images/cover.jpg"
    assert grouped[0]["front_image_job_id"] == "old"
    assert db.query_one("SELECT COUNT(*) AS total FROM jobs_history")["total"] == 2
    assert db.query_one("SELECT front_image FROM physical_media WHERE manager_job_id='old'")["front_image"] == "cover.jpg"
