from fastapi.testclient import TestClient


def test_preferences_is_a_public_app_shell_without_starting_workers():
    import main
    # No lifespan context: this checks routing without starting real pollers/movers.
    client = TestClient(main.app)
    response = client.get('/preferences')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert 'id="mobileNavToggle"' in response.text
    assert 'id="settingsWindowTitle"' in response.text
    assert 'id="settingsClose"' not in response.text
    assert '/preferences' in main.PUBLIC_PATHS
