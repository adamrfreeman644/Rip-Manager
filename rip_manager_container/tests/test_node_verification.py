import importlib.util
from pathlib import Path
from types import SimpleNamespace


NODE_FILE = (
    Path(__file__).parents[1]
    / "app"
    / "bundled_rip_node_api_v0.2.10.py"
)


def load_node():
    spec = importlib.util.spec_from_file_location("rip_node_api_v0_2_10", NODE_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def readable_video(*args, **kwargs):
    return SimpleNamespace(returncode=0, stdout="0\n", stderr="")


def test_readable_short_extra_does_not_fail_healthy_rip(tmp_path, monkeypatch):
    node = load_node()
    monkeypatch.setattr(node.subprocess, "run", readable_video)
    (tmp_path / "main.mkv").write_bytes(b"0" * node.MIN_VERIFIED_MKV_BYTES)
    (tmp_path / "logo.mkv").write_bytes(b"0" * 1024)

    result = node.verify_rip(tmp_path)

    assert result["ok"] is True
    assert result["small_files"] == 1
    assert "logo.mkv" in result["warnings"][0]


def test_only_tiny_titles_still_fails_verification(tmp_path, monkeypatch):
    node = load_node()
    monkeypatch.setattr(node.subprocess, "run", readable_video)
    (tmp_path / "tiny.mkv").write_bytes(b"0" * 1024)

    result = node.verify_rip(tmp_path)

    assert result["ok"] is False
    assert result["small_files"] == 1
    assert "No substantial MKV" in result["error"]


def test_unreadable_short_file_still_fails_verification(tmp_path, monkeypatch):
    node = load_node()
    monkeypatch.setattr(
        node.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="invalid data"
        ),
    )
    (tmp_path / "broken.mkv").write_bytes(b"0" * 1024)

    result = node.verify_rip(tmp_path)

    assert result["ok"] is False
    assert "ffprobe could not read broken.mkv" in result["error"]
