def test_optical_intake_compatibility_uses_same_module():
    import intake,disc_intake,media_intake
    assert intake is disc_intake
    assert disc_intake is not media_intake
    assert intake.queue is disc_intake.queue


def test_runtime_optical_users_reference_disc_intake():
    from pathlib import Path
    root=Path(__file__).parents[1]/"app"
    for name in ['poller.py','routes/control.py','routes/fleet.py','routes/settings.py']:
        source=(root/name).read_text()
        assert 'import disc_intake' in source
        assert 'import intake' not in source
