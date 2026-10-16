from types import SimpleNamespace
import pytest
from test_node_verification import load_node


def node_for_test(tmp_path, monkeypatch):
    node = load_node()
    monkeypatch.setattr(node, 'NODE_SETTINGS_FILE', tmp_path / 'node-settings.json')
    monkeypatch.setattr(node.psutil, 'process_iter', lambda *args: [])
    monkeypatch.setattr(node.Path, 'home', lambda: tmp_path)
    return node


def test_busy_node_defers_without_running_commands(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    node.drive_active_job['DVD1'] = 'busy'
    monkeypatch.setattr(node.subprocess, 'run', lambda *a, **k: pytest.fail('Must not run apt'))
    assert node.perform_maintenance() is False
    assert not node.maintenance_active


def test_success_persists_fortnight_schedule_and_preserves_storage(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    node.save_node_settings({'output_path': '/mnt/ripping'})
    node.save_maintenance(enabled=True, refresh_beta=True, requested=True)
    monkeypatch.setattr(node.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(node, 'refresh_beta_key', lambda: 'Beta key refreshed')
    assert node.perform_maintenance()
    state = node.maintenance_status()
    assert state['state'] == 'complete'
    assert state['next_check'] - state['last_success'] == 14 * 86400
    assert state['requested'] is False
    assert node.load_node_settings()['output_path'] == '/mnt/ripping'
    assert not node.maintenance_active


def test_failure_keeps_last_success_and_retries_in_one_hour(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    node.save_maintenance(enabled=True, refresh_beta=False, last_success=1)
    monkeypatch.setattr(node.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    assert node.perform_maintenance()
    state = node.maintenance_status()
    assert state['state'] == 'error'
    assert state['last_success'] == 1
    assert 3500 < state['next_check'] - node.time.time() <= 3600


def beta_response(monkeypatch):
    import urllib.request
    class Response:
        url = 'https://www.makemkv.com/buy/'
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return ('<code>T-' + 'x' * 65 + '</code>').encode()
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: Response())


def test_beta_refresh_preserves_other_settings_and_never_returns_key(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    beta_response(monkeypatch)
    folder = tmp_path / '.MakeMKV'
    folder.mkdir()
    path = folder / 'settings.conf'
    path.write_text('app_PreferredLanguage = "eng"\n')
    node.refresh_beta_key()
    assert 'app_PreferredLanguage = "eng"' in path.read_text()
    assert 'app_Key = "T-' in path.read_text()
    assert 'managed_beta_key' not in node.maintenance_status()
    assert not any('T-' in str(value) for value in node.maintenance_status().values())


def test_paid_registration_is_preserved(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    beta_response(monkeypatch)
    folder = tmp_path / '.MakeMKV'
    folder.mkdir()
    path = folder / 'settings.conf'
    original = 'app_Key = "personal-key"\n'
    path.write_text(original)
    assert 'preserved' in node.refresh_beta_key()
    assert path.read_text() == original


def test_external_rip_process_defers_maintenance(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    monkeypatch.setattr(node.psutil, 'process_iter', lambda *a: [SimpleNamespace(info={'name': 'makemkvcon'})])
    monkeypatch.setattr(node.subprocess, 'run', lambda *a, **k: pytest.fail('Must wait'))
    assert node.perform_maintenance() is False
    assert node.maintenance_status()['state'] == 'waiting'


def test_node_endpoints_require_bearer_token(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    monkeypatch.setattr(node, 'API_TOKEN', 'test-token')
    from fastapi import HTTPException
    for call in (node.get_maintenance, node.queue_maintenance):
        with pytest.raises(HTTPException) as exc:
            call(authorization=None)
        assert exc.value.status_code == 401
    assert node.get_maintenance(authorization='Bearer test-token')['interval_days'] == 14


def test_active_maintenance_blocks_new_rips(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    node.maintenance_active = True
    monkeypatch.setattr(node, 'get_device', lambda name: '/dev/sr0')
    monkeypatch.setattr(node, 'device_exists', lambda name: True)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        node.start_rip('DVD1', node.RipRequest(title='Film'), authorization=None)
    assert exc.value.status_code == 409
    assert 'maintenance' in exc.value.detail


def test_setup_uses_fixed_helper_and_exact_sudo_rule(monkeypatch):
    import adoption
    commands = []
    class Client:
        closed = False
        def close(self): self.closed = True
    client = Client()
    monkeypatch.setattr(adoption, '_ssh', lambda *args: client)
    def run(_client, command, sudo, **kwargs):
        commands.append(command)
        return (0, 'adam\n' if 'systemctl show' in command else '', '')
    monkeypatch.setattr(adoption, '_run', run)
    result = adoption.setup_makemkv_maintenance('node', 22, 'adam', 'secret', None)
    assert result['ok']
    assert client.closed
    import base64
    rule = base64.b64encode(b'adam ALL=(root) NOPASSWD: /usr/local/sbin/rip-makemkv-maintenance ""\n').decode()
    assert rule in commands[-1]
    assert 'secret' not in commands[-1]
    import subprocess
    assert subprocess.run(['sh', '-n'], input=commands[-1], text=True).returncode == 0
    assert subprocess.run(['sh', '-n'], input=adoption.MAKEMKV_HELPER_SCRIPT, text=True).returncode == 0
    assert '--only-upgrade' in adoption.MAKEMKV_HELPER_SCRIPT


def test_failed_key_download_keeps_existing_file(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    import urllib.request
    folder = tmp_path / '.MakeMKV'
    folder.mkdir()
    path = folder / 'settings.conf'
    original = 'app_Key = "T-' + 'a' * 65 + '"\n'
    path.write_text(original)
    def fail(*args, **kwargs): raise OSError('Offline')
    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    with pytest.raises(OSError): node.refresh_beta_key()
    assert path.read_text() == original


def test_http_controls_save_schedule_and_queue_without_waiting_for_apt(tmp_path, monkeypatch):
    node = node_for_test(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    client = TestClient(node.app)
    response = client.put('/maintenance/makemkv', json={'enabled': True, 'refresh_beta': False})
    assert response.status_code == 200
    assert response.json()['enabled'] is True
    response = client.post('/maintenance/makemkv/run')
    assert response.status_code == 200
    assert response.json()['requested'] is True
    assert response.json()['state'] == 'queued'
    assert client.get('/maintenance/makemkv').json()['interval_days'] == 14
