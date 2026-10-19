import pytest
from test_reliability import fresh_database,configure
import jobs,mover,recovery,media_prep


def transfer_for(tmp_path,monkeypatch,kind='movie'):
    db=fresh_database(tmp_path,monkeypatch)
    node=tmp_path/'node';root='TV' if kind=='tv' else 'Movies'
    source=node/root/'Film';source.mkdir(parents=True)
    (source/'movie.mkv').write_bytes(b'irreplaceable')
    media=configure(db,tmp_path,node)
    adopted=recovery.adopt('node-1','Film',kind)
    row=dict(db.query_one('SELECT * FROM transfer_queue'))
    job=jobs.row_to_dict(db.query_one('SELECT * FROM jobs_history WHERE manager_job_id=?',(adopted['manager_job_id'],)))
    return db,source,media,row,job


@pytest.mark.parametrize('linked',[False,True])
def test_unowned_partial_copies_are_never_deleted(tmp_path,monkeypatch,linked):
    db,source,media,transfer,job=transfer_for(tmp_path,monkeypatch)
    destination=media/'1 ~ Movies'/'Film';destination.parent.mkdir(parents=True)
    partial=destination.with_name(f'.Film.partial-{transfer["id"]}')
    unrelated=tmp_path/'unrelated';unrelated.mkdir();(unrelated/'keep.mkv').write_bytes(b'original media')
    if linked:
        partial.symlink_to(unrelated,target_is_directory=True)
    else:
        partial.mkdir();(partial/'keep.mkv').write_bytes(b'original media')
    with pytest.raises((ValueError,FileExistsError),match='symbolic link|Unowned'):
        mover._copy_transfer(transfer,job)
    assert (partial/'keep.mkv').read_bytes()==b'original media'
    assert (source/'movie.mkv').read_bytes()==b'irreplaceable'


def test_overlapping_destination_is_rejected_before_media_preparation(tmp_path,monkeypatch):
    db,source,media,transfer,job=transfer_for(tmp_path,monkeypatch)
    db.set_setting('mover_destination_root',str(source.parent.parent))
    import json
    db.set_setting('mover_destination_folders',json.dumps({'movie':'Movies/Film/inside'}))
    with pytest.raises(ValueError,match='non-overlapping'):
        mover._copy_transfer(transfer,job)
    assert not (source/'inside').exists()
    assert (source/'movie.mkv').read_bytes()==b'irreplaceable'


def test_failed_tv_transfer_keeps_combined_play_all_bytes(tmp_path,monkeypatch):
    db,source,media,transfer,job=transfer_for(tmp_path,monkeypatch,'tv')
    (source/'movie.mkv').rename(source/'playall.mkv')
    (source/'ep1.mkv').write_bytes(b'episode one')
    (source/'ep2.mkv').write_bytes(b'episode two')
    job.update(drive='DVD1',title='Show',season=1,disc=1)
    durations={'playall.mkv':3600,'ep1.mkv':1800,'ep2.mkv':1800}
    monkeypatch.setattr(media_prep,'_duration',lambda p:durations[p.name])
    monkeypatch.setattr(mover,'_hash',lambda p:'forced-failure')
    with pytest.raises(IOError,match='Verification failed'):
        mover._copy_transfer(transfer,job)
    assert {p.read_bytes() for p in source.rglob('*.mkv')}=={b'irreplaceable',b'episode one',b'episode two'}
    assert (source/'extras'/'Show S01 D01 Play All.mkv').read_bytes()==b'irreplaceable'
    assert not (media/'2 ~ TV'/'Film').exists()


def test_tv_preparation_retry_keeps_play_all_safely_in_extras(tmp_path,monkeypatch):
    source=tmp_path/'show';source.mkdir()
    for name,data in [('combined.mkv',b'all'),('ep1.mkv',b'one'),('ep2.mkv',b'two')]:
        (source/name).write_bytes(data)
    (source/'disc-info.txt').write_text('ready')
    monkeypatch.setattr(media_prep,'_duration',lambda p:3600 if p.read_bytes()==b'all' else 1800)
    job={'state':'complete','media_type':'tv','title':'Show','season':1,'disc':1}
    media_prep.prepare_completed_rip(source,job)
    before={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*.mkv')}
    result=media_prep.prepare_completed_rip(source,job)
    assert result['play_all_preserved']==1
    assert {p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*.mkv')}==before
