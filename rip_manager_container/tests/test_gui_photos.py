import base64
import json

import pytest

from test_archive_mover import fresh_database, add_complete_job


def upload(payload):
    return 'data:image/png;base64,' + base64.b64encode(payload).decode()


def test_extra_delete_then_add_preserves_surviving_photo(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    add_complete_job(db, '/unmounted/Test Film')
    import archive
    first = archive.save_image('rip-node-1:job-1', 'extra', upload(b'first'))
    second = archive.save_image('rip-node-1:job-1', 'extra', upload(b'second'))
    archive.delete_image('rip-node-1:job-1', 'extra', 0)
    third = archive.save_image('rip-node-1:job-1', 'extra', upload(b'third'))
    assert len({first, second, third}) == 3
    row = db.query_one('SELECT extras_json FROM physical_media')
    assert json.loads(row['extras_json']) == [second, third]
    assert archive.image_path('rip-node-1:job-1', second).read_bytes() == b'second'


def test_extra_replace_updates_selected_slot_and_preserves_other_photos(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    add_complete_job(db, '/unmounted/Test Film')
    import archive
    from routes.archive import put_image, ImageUpdate
    first = archive.save_image('rip-node-1:job-1', 'extra', upload(b'first'))
    second = archive.save_image('rip-node-1:job-1', 'extra', upload(b'second'))
    result = put_image('rip-node-1:job-1', 'extra', ImageUpdate(data_url=upload(b'replaced')), index=0)
    assert result['filename'] == first
    assert json.loads(db.query_one('SELECT extras_json FROM physical_media')['extras_json']) == [first, second]
    assert archive.image_path('rip-node-1:job-1', first).read_bytes() == b'replaced'
    assert archive.image_path('rip-node-1:job-1', second).read_bytes() == b'second'
    with pytest.raises(ValueError, match='no longer exists'):
        archive.save_image('rip-node-1:job-1', 'extra', upload(b'invalid'), index=4)


def test_failed_replacement_keeps_existing_photo(tmp_path, monkeypatch):
    fresh_database(tmp_path, monkeypatch)
    import db, archive
    add_complete_job(db, '/unmounted/Test Film')
    name = archive.save_image('rip-node-1:job-1', 'front', upload(b'original'))
    def fail(*args):
        raise OSError('storage disconnected')
    monkeypatch.setattr(archive.os, 'replace', fail)
    with pytest.raises(OSError):
        archive.save_image('rip-node-1:job-1', 'front', upload(b'new'))
    assert archive.image_path('rip-node-1:job-1', name).read_bytes() == b'original'
