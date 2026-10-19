import json
import os
from pathlib import Path


def test_lookup_reuses_unchanged_manifest_and_detects_replacements(tmp_path, monkeypatch):
    import library
    library._cached_lookup_manifest.cache_clear()
    path = tmp_path / 'disc-info.json'
    path.write_text(json.dumps({'title': 'Film A', 'year': 2000, 'files': ['large detail'] * 1000}))
    reads = []
    real = library._read_manifest
    def read(target):
        reads.append(target)
        return real(target)
    monkeypatch.setattr(library, '_read_manifest', read)
    first = library._lookup_manifest(path)
    first['title'] = 'Caller mutation'
    assert library._lookup_manifest(path)['title'] == 'Film A'
    assert len(reads) == 1
    assert 'files' not in first
    old = path.stat()
    path.write_text(json.dumps({'title': 'Film B', 'year': 2000, 'files': ['large detail'] * 1000}))
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert library._lookup_manifest(path)['title'] == 'Film B'
    replacement = path.with_suffix('.new')
    replacement.write_text('{"title":"Replacement"}')
    replacement.replace(path)
    assert library._lookup_manifest(path)['title'] == 'Replacement'
    path.unlink()
    assert library._lookup_manifest(path) == {}
    path.write_text('{"title":"Restored"}')
    assert library._lookup_manifest(path)['title'] == 'Restored'
    assert library._cached_lookup_manifest.cache_info().maxsize == 2048


def test_lookup_does_not_cache_transient_read_failures(tmp_path, monkeypatch):
    import library
    path = tmp_path / 'disc-info.json'; path.write_text('{}')
    responses = iter([None, {'title': 'Recovered'}])
    monkeypatch.setattr(library, '_read_manifest', lambda path: next(responses))
    assert library._lookup_manifest(path) == {}
    assert library._lookup_manifest(path)['title'] == 'Recovered'
