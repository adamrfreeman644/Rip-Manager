import json
import pytest
from fastapi import HTTPException
import downloads,archive
from test_download_intake import setup,add_rows


def single_job(folder,destination='movie',context=None):
    with downloads.connection() as con:
        cur=con.execute("INSERT INTO download_jobs(url,media_type,quality,subtitles,playlist,destination,status,staging_dir,title,completion_json) VALUES (?,'video','best',1,0,?,'completed',?,'Saved title',?)",('https://youtu.be/source',destination,str(folder),json.dumps(context) if context else None))
        return cur.lastrowid


def read_job(job_id):
    with downloads.connection() as con:
        return dict(con.execute('SELECT * FROM download_jobs WHERE id=?',(job_id,)).fetchone())


@pytest.mark.parametrize('phase',['manifest','event'])
def test_retry_restores_provenance_after_failure_without_downloading_again(tmp_path,monkeypatch,phase):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    job_id=single_job(folder)
    downloads.update(job_id,status='running')
    calls=[]
    class Downloader:
        def __init__(self,options): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def extract_info(self,url,download):
            calls.append(url);assert download
            return {'title':'Original provider title','thumbnail':'https://example.com/cover.jpg'}
    monkeypatch.setattr(downloads.yt_dlp,'YoutubeDL',Downloader)
    record=downloads._record_download;event=archive.record_process_event
    if phase=='manifest':
        def failed(*args): raise OSError('disk unavailable')
        monkeypatch.setattr(downloads,'_record_download',failed)
    else:
        monkeypatch.setattr(archive,'record_process_event',lambda *args:False)
    downloads.run_job(read_job(job_id))
    saved=read_job(job_id);context=json.loads(saved['completion_json'])
    assert saved['status']=='completed' and saved['error']
    assert context['title']=='Original provider title'
    assert not db.query('SELECT * FROM transfer_queue')
    monkeypatch.setattr(downloads,'_record_download',record)
    monkeypatch.setattr(archive,'record_process_event',event)
    monkeypatch.setattr(downloads.yt_dlp,'YoutubeDL',lambda *args:pytest.fail('History repair must not download again'))
    first=downloads.retry_intake(job_id)
    second=downloads.retry_intake(job_id)
    assert first['manager_job_id']==second['manager_job_id']
    payload=json.loads((folder/'disc-info.json').read_text())
    assert len(payload['downloads'])==1
    entry=payload['downloads'][0]
    assert entry['title']==context['title'] and entry['source_url']==context['source_url']
    assert entry['completed_at']==context['completed_at'] and entry['outcome']=='downloaded_and_processed'
    assert len([e for e in payload['process_history'] if e['event']=='download_finished'])==1
    assert len(calls)==1 and len(db.query('SELECT * FROM transfer_queue'))==1
    assert (folder/'media.mkv').read_bytes()==b'media'


def test_playlist_retry_restores_every_completed_members_history(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    row=add_rows()
    with downloads.connection() as con:
        for number in (1,2):
            con.execute("UPDATE download_jobs SET status='completed',staging_dir=?,completion_json=? WHERE id=?",(str(folder),json.dumps({'title':f'Video {number}','completed_at':100+number}),number))
    (folder/'second.mkv').write_bytes(b'second')
    downloads.retry_intake(row['id'])
    payload=json.loads((folder/'disc-info.json').read_text())
    assert {x['title'] for x in payload['downloads']}=={'Video 1','Video 2'}
    assert {x['source_url'] for x in payload['downloads']}=={'https://youtu.be/1','https://youtu.be/2'}
    assert all(x['playlist_url']==row['playlist_url'] for x in payload['downloads'])
    assert len(db.query('SELECT * FROM transfer_queue'))==1


def test_failed_history_repair_does_not_queue_or_mark_download_failed(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    job_id=single_job(folder)
    def failed(*args): raise OSError('read-only manifest')
    monkeypatch.setattr(downloads,'_record_download',failed)
    with pytest.raises(HTTPException) as error: downloads.retry_intake(job_id)
    assert error.value.status_code==422
    assert not db.query('SELECT * FROM transfer_queue')
    assert read_job(job_id)['status']=='completed'
    assert 'read-only manifest' in read_job(job_id)['error']
    assert (folder/'media.mkv').read_bytes()==b'media'


def test_legacy_history_retry_uses_saved_fields_without_inventing_time(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    job_id=single_job(folder)
    downloads.retry_intake(job_id)
    entry=json.loads((folder/'disc-info.json').read_text())['downloads'][0]
    assert entry['title']=='Saved title' and entry['completed_at'] is None
    assert entry['source_url']=='https://youtu.be/source'


def test_plain_download_can_repair_history_without_queueing_into_intake(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    target=media/'Downloads';target.mkdir();(target/'video.mkv').write_bytes(b'plain download')
    job_id=single_job(target,'downloads')
    downloads.update(job_id,error='provenance failed')
    result=downloads.retry_intake(job_id)
    assert result['provenance_repaired'] and not result['queued']
    assert read_job(job_id)['error'] is None
    assert not db.query('SELECT * FROM transfer_queue')
    assert (target/'video.mkv').read_bytes()==b'plain download'


def test_history_retry_respects_disabled_then_enabled_mover(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    job_id=single_job(folder,context={'title':'Original','completed_at':123})
    db.set_setting('mover_enabled',0)
    with pytest.raises(HTTPException) as error: downloads.retry_intake(job_id)
    assert error.value.status_code==409
    db.set_setting('mover_enabled',1)
    downloads.retry_intake(job_id)
    payload=json.loads((folder/'disc-info.json').read_text())
    assert len(payload['downloads'])==1 and payload['downloads'][0]['completed_at']==123
    assert len(db.query('SELECT * FROM transfer_queue'))==1


def test_history_retry_does_not_mutate_source_during_mover_verification(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    job_id=single_job(folder)
    downloads.retry_intake(job_id)
    before=(folder/'disc-info.json').read_bytes()
    with db.write() as con: con.execute("UPDATE transfer_queue SET state='verifying'")
    with pytest.raises(HTTPException) as error: downloads.retry_intake(job_id)
    assert error.value.status_code==409
    assert (folder/'disc-info.json').read_bytes()==before
