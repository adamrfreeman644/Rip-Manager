import pytest
from test_reliability import fresh_database,configure
import archive,recovery


@pytest.mark.parametrize('state',['failed','cancelled','queued'])
def test_generated_sidecars_do_not_create_new_recovery_jobs(tmp_path,monkeypatch,state):
    db=fresh_database(tmp_path,monkeypatch)
    mount=tmp_path/'node';configure(db,tmp_path,mount)
    source=mount/'Movies'/'Film';source.mkdir(parents=True)
    (source/'film.mkv').write_bytes(b'original media')
    first=recovery.adopt('node-1','Film')
    archive.sync_sidecars(first['manager_job_id'],source)
    (source/'rip-manager-prep.json').write_text('{"prepared":1}')
    with db.write() as con:
        con.execute('UPDATE transfer_queue SET state=?,error=?',(state,'interrupted'))
    second=recovery.adopt('node-1','Film')
    assert second['manager_job_id']==first['manager_job_id']
    assert len(db.query('SELECT * FROM jobs_history'))==1
    assert len(db.query('SELECT * FROM transfer_queue'))==1
    assert db.query_one('SELECT state FROM transfer_queue')['state']=='queued'
    if state in {'failed','cancelled'}:
        assert second['retried']
    assert (source/'film.mkv').read_bytes()==b'original media'


def test_recovery_retry_does_not_override_disabled_mover(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    mount=tmp_path/'node';configure(db,tmp_path,mount)
    source=mount/'Movies'/'Film';source.mkdir(parents=True);(source/'film.mkv').write_bytes(b'media')
    recovery.adopt('node-1','Film')
    with db.write() as con: con.execute("UPDATE transfer_queue SET state='failed'")
    db.set_setting('mover_enabled',0)
    with pytest.raises(ValueError,match='disabled'):
        recovery.adopt('node-1','Film')
    assert db.query_one('SELECT state FROM transfer_queue')['state']=='failed'
