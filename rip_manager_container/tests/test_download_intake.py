import json
from pathlib import Path
from test_intake_pipeline import stage
import downloads


def setup(tmp_path,monkeypatch):
    db,media,intake,folder=stage(tmp_path,monkeypatch)
    monkeypatch.setattr(downloads,"DB_PATH",db.DB_PATH)
    downloads.initialize()
    return db,media,intake,folder


def add_rows(group="g"):
    with downloads.connection() as con:
        for n in (1,2):
            con.execute("INSERT INTO download_jobs(url,media_type,quality,subtitles,playlist,destination,status,group_id,playlist_url) VALUES (?,'video','best',0,0,'movie',?,?,?)",(f"https://youtu.be/{n}","completed" if n==1 else "failed",group,"https://youtube.com/playlist?list=x"))
        return dict(con.execute("SELECT * FROM download_jobs WHERE id=1").fetchone())


def test_playlist_queues_once_only_after_all_members_complete(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    row=add_rows()
    monkeypatch.setattr(intake,"scan",lambda: (_ for _ in ()).throw(AssertionError("global scan")))
    downloads._queue_finished_group(row,folder)
    assert not db.query("SELECT * FROM transfer_queue")
    with downloads.connection() as con:
        con.execute("UPDATE download_jobs SET status='completed'")
    downloads._queue_finished_group(row,folder)
    downloads._queue_finished_group(row,folder)
    assert len(db.query("SELECT * FROM transfer_queue"))==1
    with downloads.connection() as con:
        assert len({r[0] for r in con.execute("SELECT manager_job_id FROM download_jobs")})==1


def test_download_provenance_survives_mover_and_destination_is_in_history(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    row=add_rows()
    downloads._record_download(row,folder,{"title":"Video one"})
    with downloads.connection() as con:
        con.execute("UPDATE download_jobs SET status='completed'")
    downloads._queue_finished_group(row,folder)
    import mover,jobs
    transfer=dict(db.query_one("SELECT * FROM transfer_queue"))
    job=jobs.row_to_dict(db.query_one("SELECT * FROM jobs_history WHERE manager_job_id=?",(transfer["manager_job_id"],)))
    mover._copy_transfer(transfer,job)
    destination=media/"1 ~ Movies"/folder.name
    manifest=json.loads((destination/"disc-info.json").read_text())
    assert manifest["downloads"][0]["source_url"]==row["url"]
    assert manifest["downloads"][0]["playlist_url"]==row["playlist_url"]
    assert downloads.list_jobs()[0]["transfer"]["destination_dir"]==str(destination)


def test_playlist_add_persists_shared_staging_and_member_selection(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(downloads,"extract_playlist",lambda url:{"_type":"playlist","title":"My Playlist"})
    monkeypatch.setattr(downloads,"playlist_entries",lambda info:[{"index":1,"title":"One","url":"https://youtu.be/one"},{"index":2,"title":"Two","url":"https://youtu.be/two"}])
    result=downloads.add(downloads.AddJob(url="https://youtube.com/playlist?list=x",media_type="video",quality="best",playlist=True,destination="movie"))
    assert result["count"]==2
    with downloads.connection() as con:
        rows=[dict(r) for r in con.execute("SELECT * FROM download_jobs")]
    assert len({r["staging_dir"] for r in rows})==1
    assert len({r["group_id"] for r in rows})==1
    assert all(r["playlist"]==0 for r in rows)


def test_removing_failed_playlist_member_allows_completed_group_to_queue(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    row=add_rows()
    with downloads.connection() as con:
        con.execute("UPDATE download_jobs SET staging_dir=?",(str(folder),))
    downloads.remove(2)
    assert len(db.query("SELECT * FROM transfer_queue"))==1
