import sqlite3

import pytest
from fastapi import HTTPException


def fresh_database(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rip-manager.db")
    try:
        old = getattr(db._local, "conn", None)
        if old is not None:
            old.close()
    except Exception:
        pass
    db._local.conn = None
    db._settings_cache.clear()
    db.init_db()
    return db


def test_controller_pin_is_salted_hash_not_plaintext(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    import auth
    encoded = auth.hash_pin("1234")
    assert encoded.startswith("pbkdf2_sha256$")
    assert encoded != "1234"
    assert auth.verify_pin("1234", encoded)
    assert not auth.verify_pin("4321", encoded)


def test_pin_change_hashes_and_revokes_sessions(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    import auth
    db.set_setting("pin_hash", auth.hash_pin("1234"))
    with db.write() as conn:
        conn.execute("INSERT INTO auth_sessions(token_hash,created_at,expires_at) VALUES(?,?,?)", ("abc", 1, 99999999999))
    db.set_setting("pin_hash", auth.hash_pin("5678"))
    auth.revoke_all_sessions()
    assert auth.verify_pin("5678", db.get_setting("pin_hash"))
    assert not auth.verify_pin("1234", db.get_setting("pin_hash"))
    assert db.query_one("SELECT token_hash FROM auth_sessions") is None


def test_console_recovery_preserves_configuration_and_nodes(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    import controller_admin
    db.set_setting("theme", "light")
    db.set_setting("dashboard_columns", "4")
    with db.write() as conn:
        conn.execute("INSERT INTO nodes(id,name,url,enabled,token) VALUES(?,?,?,?,?)", ("node-1", "Node One", "http://node:8000", 1, "node-secret-token"))
    answers = iter(["2468", "2468"])
    monkeypatch.setattr(controller_admin.getpass, "getpass", lambda prompt: next(answers))
    assert controller_admin.reset_pin(False) == 0
    assert db.get_setting("theme") == "light"
    assert db.get_setting("dashboard_columns") == "4"
    node = db.query_one("SELECT id,name,url,token FROM nodes WHERE id='node-1'")
    assert node["name"] == "Node One"
    assert node["token"] == "node-secret-token"
    assert controller_admin.auth.verify_pin("2468", db.get_setting("pin_hash"))
    assert db.get_setting_bool("lock_enabled")


def test_node_security_status_never_returns_token(tmp_path, monkeypatch):
    db = fresh_database(tmp_path, monkeypatch)
    with db.write() as conn:
        conn.execute("INSERT INTO nodes(id,name,url,enabled,token) VALUES(?,?,?,?,?)", ("node-1", "One", "http://one", 1, "super-secret"))
        conn.execute("INSERT INTO nodes(id,name,url,enabled,token) VALUES(?,?,?,?,?)", ("node-2", "Two", "http://two", 1, None))
    from routes.auth import node_auth_status
    result = node_auth_status()
    assert result == {"state": "partial", "configured_nodes": 2, "authenticated_nodes": 1}
    assert "super-secret" not in repr(result)


def test_node_client_sends_bearer_token_without_human_pin():
    import nodes
    row = {"token": "machine-only-secret"}
    assert nodes.headers_for(row) == {"Authorization": "Bearer machine-only-secret"}


def test_health_security_contract_is_basic():
    # Human credentials are never part of the documented health response.
    from main import health
    result = health()
    assert set(result) == {"ok", "time"}
