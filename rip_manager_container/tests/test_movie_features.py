from pathlib import Path
import pytest
import media_prep


def prepare(tmp_path, monkeypatch, durations, order=None):
    source = tmp_path / "Film"
    source.mkdir()
    (source / "disc-info.txt").write_text("complete")
    for name in durations:
        (source / name).write_bytes(name.encode())
    monkeypatch.setattr(media_prep, "_duration", lambda p: durations[p.name])
    if order:
        monkeypatch.setattr(media_prep, "_videos", lambda root: [source / n for n in order])
    report = media_prep.prepare_completed_rip(source, {"title": "Film", "state": "complete", "media_type": "movie"})
    return source, report


@pytest.mark.parametrize("order", [["extra.mkv", "feature.mkv"], ["feature.mkv", "extra.mkv"]])
def test_selected_main_always_gets_canonical_name(tmp_path, monkeypatch, order):
    source, report = prepare(tmp_path, monkeypatch, {"extra.mkv": 120, "feature.mkv": 7200}, order)
    assert (source / "Film.mkv").read_bytes() == b"feature.mkv"
    assert report["main_feature"] == "Film.mkv"
    assert len(list((source / "extras").glob("*.mkv"))) == 1


def test_shorter_named_cut_is_preserved_as_feature(tmp_path, monkeypatch):
    source, report = prepare(tmp_path, monkeypatch, {"Extended.mkv": 10000, "Theatrical.mkv": 6000, "Directors Cut.mkv": 7000, "trailer.mkv": 120})
    assert (source / "Film.mkv").read_bytes() == b"Theatrical.mkv"
    assert len(list(source.glob("*.mkv"))) == 3
    assert any("Extended" in p.name for p in source.glob("*.mkv"))
    assert any("Directors Cut" in p.name for p in source.glob("*.mkv"))
    assert report["extras"] == 1


def test_old_canonical_extra_cannot_steal_main_name(tmp_path, monkeypatch):
    source, report = prepare(tmp_path, monkeypatch, {"Film.mkv": 120, "feature.mkv": 7200})
    assert (source / "Film.mkv").read_bytes() == b"feature.mkv"
    assert list((source / "extras").glob("*.mkv"))


def test_preparation_retry_keeps_the_selected_cut(tmp_path, monkeypatch):
    source, report = prepare(tmp_path, monkeypatch, {"Extended.mkv": 10000, "Theatrical.mkv": 6000})
    monkeypatch.setattr(media_prep, "_duration", lambda p: 6000 if p.name == "Film.mkv" else 10000)
    media_prep.prepare_completed_rip(source, {"title": "Film", "state": "complete", "media_type": "movie"})
    assert (source / "Film.mkv").read_bytes() == b"Theatrical.mkv"
