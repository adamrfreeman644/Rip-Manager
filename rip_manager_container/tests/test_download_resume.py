from pathlib import Path
import pytest
import downloads
from test_download_intake import setup


@pytest.mark.parametrize('suffix',['.mp4.part','.mp4.ytdl','.mp4.part-Frag12'])
def test_existing_job_can_resume_temporary_artifacts(tmp_path,suffix):
    row={'custom_name':'My [Video]','playlist':0,'staging_dir':str(tmp_path)}
    artifact=tmp_path/('My [Video]'+suffix);artifact.write_bytes(b'partial')
    downloads._check_output_collision(row,tmp_path)
    assert artifact.read_bytes()==b'partial'


def test_completed_collision_remains_protected_when_partial_also_exists(tmp_path):
    row={'custom_name':'My [Video]','playlist':0,'staging_dir':str(tmp_path)}
    (tmp_path/'My [Video].mp4.part').write_bytes(b'partial')
    complete=tmp_path/'My [Video].mkv';complete.write_bytes(b'existing complete media')
    with pytest.raises(ValueError,match='already exists'):
        downloads._check_output_collision(row,tmp_path)
    assert complete.read_bytes()==b'existing complete media'


def test_new_job_cannot_claim_another_downloads_partial_file(tmp_path):
    row={'custom_name':'My Video','playlist':0,'staging_dir':None}
    (tmp_path/'My Video.mp4.part').write_bytes(b'other job')
    with pytest.raises(ValueError,match='already exists'):
        downloads._check_output_collision(row,tmp_path)


def test_failed_download_retries_into_same_staging_folder_and_resumes(tmp_path,monkeypatch):
    db,media,intake,folder=setup(tmp_path,monkeypatch)
    result=downloads.add(downloads.AddJob(url='https://youtu.be/test',media_type='video',quality='best',destination='downloads',custom_name='My Video'))
    job_id=result['id'];attempts=[]
    class Downloader:
        def __init__(self,options): self.options=options
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def extract_info(self,url,download):
            assert download and self.options['continuedl'] and self.options['overwrites'] is False
            target=Path(self.options['paths']['home']);partial=target/'My Video.mp4.part'
            attempts.append(target)
            if len(attempts)==1:
                partial.write_bytes(b'first half')
                raise IOError('connection interrupted')
            assert partial.read_bytes()==b'first half'
            (target/'My Video.mkv').write_bytes(b'complete media')
            partial.unlink()
            return {'title':'My Video'}
    monkeypatch.setattr(downloads.yt_dlp,'YoutubeDL',Downloader)
    monkeypatch.setattr(downloads,'_record_download',lambda *args:None)
    def row():
        with downloads.connection() as con:
            return dict(con.execute('SELECT * FROM download_jobs WHERE id=?',(job_id,)).fetchone())
    with pytest.raises(IOError,match='interrupted'): downloads.run_job(row())
    downloads.update(job_id,status='failed',error='connection interrupted')
    downloads.retry(job_id)
    downloads.run_job(row())
    assert attempts[0]==attempts[1]
    assert row()['status']=='completed'
    assert (attempts[0]/'My Video.mkv').read_bytes()==b'complete media'
