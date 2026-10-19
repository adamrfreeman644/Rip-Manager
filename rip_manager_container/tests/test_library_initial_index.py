import threading
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_reliability import fresh_database,configure
import library,library_maintenance as maintenance
from routes.library import router


def client_for(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    media=configure(db,tmp_path,tmp_path/'node')
    folder=media/'1 ~ Movies'/'Film';folder.mkdir(parents=True)
    (folder/'disc-info.json').write_text('{"title":"Film","upc":"12345678","media_type":"movie"}')
    app=FastAPI();app.include_router(router)
    return db,TestClient(app)


def test_collection_first_load_returns_progress_without_waiting_for_scan(tmp_path,monkeypatch):
    db,client=client_for(tmp_path,monkeypatch)
    entered=threading.Event();release=threading.Event();real=library.check_manifests
    def blocked(progress):
        progress(0,1,'Film');entered.set()
        assert release.wait(10)
        return real(progress=progress)
    monkeypatch.setattr(library,'check_manifests',blocked)
    from routes import library as routes
    async def no_provider(*args): pytest.fail('Owned UPC lookup must wait for index')
    monkeypatch.setattr(routes.upc,'lookup',no_provider)
    try:
        first=client.get('/api/collection').json()
        assert first['indexing'] and entered.wait(5)
        assert not release.is_set()
        second=client.get('/api/collection').json()
        assert second['index_job']['id']==first['index_job']['id']
        smart=client.get('/api/collection/smart/12345678').json()
        assert smart['state']=='indexing'
        assert smart['index_job']['current']=='Film'
    finally:
        release.set();maintenance._future.result(timeout=10)
    ready=client.get('/api/collection').json()
    assert not ready['indexing'] and ready['index_job'] is None
    assert ready['items'][0]['title']=='Film'
    assert client.get('/api/collection/smart/12345678').json()['state']=='owned'


def test_cached_search_does_not_recursively_build_an_empty_index(tmp_path,monkeypatch):
    client_for(tmp_path,monkeypatch)
    monkeypatch.setattr(library,'check_manifests',lambda **kwargs:pytest.fail('Synchronous indexing from search'))
    assert library.search()==[]


def test_failed_initial_index_is_visible_until_explicit_retry(tmp_path,monkeypatch):
    db,client=client_for(tmp_path,monkeypatch)
    real=library.check_manifests
    def failed(progress): raise OSError('storage unavailable')
    monkeypatch.setattr(library,'check_manifests',failed)
    client.get('/api/collection');maintenance._future.result(timeout=10)
    first=client.get('/api/collection').json()
    assert first['index_job']['status']=='failed'
    assert 'storage unavailable' in first['index_job']['error']
    assert client.get('/api/collection').json()['index_job']['id']==first['index_job']['id']
    monkeypatch.setattr(library,'check_manifests',real)
    retry=client.post('/api/collection/maintenance',json={'kind':'index'}).json()
    assert retry['id']!=first['index_job']['id']
    maintenance._future.result(timeout=10)
    assert client.get('/api/collection').json()['items'][0]['title']=='Film'


def test_completed_empty_initial_index_does_not_start_another_job_on_each_get(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    configure(db,tmp_path,tmp_path/'node')
    app=FastAPI();app.include_router(router);client=TestClient(app)
    client.get('/api/collection');maintenance._future.result(timeout=10)
    monkeypatch.setattr(library,'check_manifests',lambda **kwargs:pytest.fail('Repeated empty-index scan'))
    assert client.get('/api/collection').json()['index_job'] is None
