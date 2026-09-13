import json

import pytest
from fastapi import HTTPException


def fresh_database(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rip-manager.db")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
    db._local.conn = None
    db._settings_cache.clear()
    db.init_db()
    return db


def test_drive_is_enabled_by_default(tmp_path, monkeypatch):
    fresh_database(tmp_path, monkeypatch)
    import nodes
    assert nodes.drive_preference("node-1", "DVD1", "enabled", True)


def test_do_not_use_drive_is_rejected_by_control_routes(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    db.set_setting("drive_preferences", json.dumps({
        "node-1:DVD1": {"enabled": False, "open_tray": True, "close_tray": True}
    }))
    import nodes
    from routes.control import _ensure_drive_enabled

    assert not nodes.drive_preference("node-1", "dvd1", "enabled", True)
    assert nodes.drive_preference("node-1", "dvd1", "open_tray", True)
    with pytest.raises(HTTPException) as error:
        _ensure_drive_enabled("node-1", "DVD1")
    assert error.value.status_code == 403
    assert "Do not use" in error.value.detail
