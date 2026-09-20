from pathlib import Path
import archive

def test_infers_year_and_clean_title_from_folder():
    got=archive._infer_folder_metadata(Path("The Matrix (1999)"))
    assert got["year"]==1999
    assert got["title"]=="The Matrix"

def test_inventory_summary_includes_audio_video_duration():
    files=[{"extension":".mkv","duration_seconds":90.5,"streams":[{"codec_name":"hevc","width":3840,"height":2160}]},{"extension":".flac","duration_seconds":9.5,"streams":[{"codec_name":"flac"}]}]
    got=archive._inventory_summary(files)
    assert got["total_duration_seconds"]==100.0
    assert got["audio_file_count"]==1 and got["video_file_count"]==1
    assert got["codecs"]==["flac","hevc"]
    assert got["video_resolutions"]==["3840x2160"]
