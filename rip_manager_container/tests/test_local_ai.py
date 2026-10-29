import json
from pathlib import Path
import pytest
import local_ai
from test_archive_mover import fresh_database, add_complete_job

@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    monkeypatch.setattr(local_ai, 'DATA_DIR', tmp_path / 'data')
    media = tmp_path / 'media'; folder = media / 'Movies' / 'Test Film'; folder.mkdir(parents=True)
    db.set_setting('mover_destination_root', str(media))
    add_complete_job(db, str(folder))
    with db.write() as conn:
        conn.execute("INSERT INTO physical_media(manager_job_id,final_dir,created_at,updated_at) VALUES (?,?,?,?)", ('rip-node-1:job-1',str(folder),0,0))
    (folder/'disc-info.json').write_text('{"title":"Test Film"}')
    monkeypatch.setattr(local_ai.archive, 'file_metadata', lambda p, *a: {'path':p.name,'duration_seconds':7200})
    return db, folder, monkeypatch

def propose(setup, files):
    db, folder, mp = setup
    for name, role in files:
        p=folder/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(name.encode())
    mp.setattr(local_ai, '_call', lambda *a: json.dumps({'files':[{'path':n,'role':r,'reason':'evidence'} for n,r in files]}))
    return local_ai.propose('rip-node-1:job-1')

def test_apply_and_undo_preserve_bytes_and_manifest(setup):
    plan=propose(setup,[('feature.mkv','feature')]); folder=setup[1]
    assert plan['operations'][0]['automatic']
    local_ai.apply(plan['id'])
    assert (folder/'Test Film (2026).mkv').read_bytes()==b'feature.mkv'
    local_ai.undo(plan['id'])
    assert (folder/'feature.mkv').read_bytes()==b'feature.mkv'
    assert json.loads((folder/'disc-info.json').read_text())['change_log']

def test_stale_plan_cannot_change_modified_file(setup):
    plan=propose(setup,[('feature.mkv','feature')]); (setup[1]/'feature.mkv').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'): local_ai.apply(plan['id'])

def test_unknown_tracks_never_auto_apply(setup):
    plan=propose(setup,[('t00.mkv','episode'),('t01.mkv','unknown')])
    assert not plan['operations'] and len(plan['review'])==2
    with pytest.raises(ValueError,match='review'): local_ai.apply(plan['id'],automatic=True)

def test_associated_subtitles_require_review(setup):
    (setup[1]/'feature.en.srt').write_text('subtitle')
    plan=propose(setup,[('feature.mkv','feature')])
    assert not plan['operations'] and plan['review']

def test_labelled_extras_move_without_deletion(setup):
    plan=propose(setup,[('Trailer.mkv','extra'),('t00.mkv','unknown')]);folder=setup[1]
    local_ai.apply(plan['id'])
    assert (folder/'extras/Trailer.mkv').read_bytes()==b'Trailer.mkv'
    assert (folder/'t00.mkv').exists()

def test_symlink_rejected(setup,tmp_path):
    (setup[1]/'escape').symlink_to(tmp_path)
    with pytest.raises(ValueError,match='links'): local_ai.propose('rip-node-1:job-1')

def test_model_cannot_inject_paths(setup):
    db,folder,mp=setup;(folder/'feature.mkv').write_bytes(b'data')
    mp.setattr(local_ai,'_call',lambda *a:json.dumps({'files':[{'path':'../escape.mkv','role':'feature','reason':'x'}]}))
    with pytest.raises(ValueError,match='unknown'): local_ai.propose('rip-node-1:job-1')

def test_undo_refuses_changed_media(setup):
    plan=propose(setup,[('feature.mkv','feature')]);local_ai.apply(plan['id'])
    (setup[1]/'Test Film (2026).mkv').write_bytes(b'edited')
    with pytest.raises(ValueError,match='changed'): local_ai.undo(plan['id'])

def test_destination_collision_is_reviewed(setup):
    plan=propose(setup,[('extras/Trailer.mkv','extra'),('Trailer.mkv','extra')])
    assert not plan['operations'] and plan['review']

def test_weekly_only_works_on_new_inventory(setup):
    plan=propose(setup,[('Trailer.mkv','extra')])
    assert local_ai.run_weekly()['checked']==0

def test_explicit_episode_number_retained(setup):
    with setup[0].write() as conn: conn.execute("UPDATE jobs_history SET media_type='tv' WHERE manager_job_id='rip-node-1:job-1'")
    plan=propose(setup,[('Episode.S02E04.mkv','episode')])
    assert plan['operations'][0]['target']=='Season 02/Test Film - S02E04.mkv'

@pytest.mark.parametrize('url',['http://example.com','http://169.254.169.254','http://0.0.0.0','http://192.168.1.1/path','http://user:pass@192.168.1.1'])
def test_reject_invalid_server(setup,url):
    with pytest.raises(ValueError): local_ai.save_config({'base_url':url,'model':'adam-local','weekly':False,'auto_apply':False})

def test_local_ai_api_uses_controller_lock(setup):
    import main
    from fastapi.testclient import TestClient
    setup[0].set_setting('lock_enabled',1)
    client=TestClient(main.app)
    assert client.get('/api/local-ai').status_code==401
    assert client.post('/api/local-ai/plans',json={'job_id':'rip-node-1:job-1'}).status_code==401
    setup[0].set_setting('lock_enabled',0)
    assert client.get('/api/local-ai').status_code==200
    assert client.get('/local-ai').status_code==200

def test_movie_cut_label_is_retained(setup):
    plan=propose(setup,[('Extended.mkv','feature')])
    assert '[Extended]' in plan['operations'][0]['target']

def test_explicit_trailer_label_overrules_bad_model_role(setup):
    plan=propose(setup,[('Trailer.mkv','episode')])
    assert plan['operations'][0]['target']=='extras/Trailer.mkv'

def test_weekly_auto_apply_does_not_reorganise_same_files(setup):
    db,folder,mp=setup
    (folder/'Trailer.mkv').write_bytes(b'trailer')
    mp.setattr(local_ai,'_call',lambda *a:json.dumps({'files':[{'path':'Trailer.mkv','role':'extra','reason':'label'}]}))
    db.set_setting('local_ai_auto_apply',1)
    assert local_ai.run_weekly()['applied']==1
    assert local_ai.run_weekly()['checked']==0

def test_rename_refuses_external_destination_without_overwrite(setup):
    folder=setup[1]; (folder/'a.mkv').write_bytes(b'a'); (folder/'b.mkv').write_bytes(b'b')
    with pytest.raises(FileExistsError): local_ai._move(folder,'a.mkv','b.mkv')
    assert (folder/'a.mkv').read_bytes()==b'a' and (folder/'b.mkv').read_bytes()==b'b'

def test_ai_node_name_is_saved_with_connection(setup):
    saved=local_ai.save_config({'name':'AI Node 1 — MacBook M1','base_url':'http://192.168.1.167:11434','model':'adam-local:latest','weekly':False,'auto_apply':False})
    assert saved['name']=='AI Node 1 — MacBook M1'
    assert local_ai.config()==saved
