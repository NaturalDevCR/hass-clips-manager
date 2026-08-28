import json

from cinema_collections_worker.probe import MediaProbeResult, ProbeClient


def test_probe_parses_streams_and_size(tmp_path, monkeypatch):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"x")
    payload = {
        "format": {"duration": "12.5", "size": "42"},
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "30000/1001"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    seen = {}

    class P:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        def communicate(self, timeout):
            return json.dumps(payload).encode(), b""

        def kill(self):
            pass

        returncode = 0

    monkeypatch.setattr("cinema_collections_worker.probe.subprocess.Popen", P)
    result = ProbeClient(timeout_seconds=2).probe(source)
    assert isinstance(result, MediaProbeResult)
    assert result.valid and result.duration_seconds == 12.5
    assert (result.width, result.height, result.frame_rate) == (1920, 1080, 30000 / 1001)
    assert result.has_audio and result.size_bytes == 42
    assert seen["shell"] is False


def test_probe_classifies_malformed_media_without_path(monkeypatch, tmp_path):
    source = tmp_path / "secret-name.mp4"
    source.write_bytes(b"x")

    class P:
        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, timeout):
            return b"not json", b"private /secret-name.mp4"

        def kill(self):
            pass

        returncode = 1

    monkeypatch.setattr("cinema_collections_worker.probe.subprocess.Popen", P)
    result = ProbeClient().probe(source)
    assert not result.valid
    assert "secret-name" not in (result.error or "")
