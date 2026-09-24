import json

from cinema_collections_worker.probe import MediaProbeResult, ProbeClient


def test_probe_parses_streams_and_size(tmp_path, monkeypatch):
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"x")
    payload = {
        "format": {"duration": "12.5", "size": "42"},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
                "duration": "12.5",
            },
            {"codec_type": "audio", "codec_name": "aac", "duration": "12.48"},
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
    assert result.video_duration_seconds == 12.5
    assert result.audio_duration_seconds == 12.48
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


def test_output_validation_rejects_materially_unsynchronized_streams():
    from cinema_collections_worker.jobs import JobWorker
    from cinema_collections_worker.profile_validation import ProcessingProfile

    result = MediaProbeResult(
        valid=True,
        duration_seconds=12.5,
        width=3840,
        height=2160,
        frame_rate=24,
        has_audio=True,
        video_duration_seconds=12.5,
        audio_duration_seconds=12.8,
    )

    assert not JobWorker._valid_output(result, ProcessingProfile())


def test_zero_tail_margin_keeps_content_through_the_final_file_timestamp():
    from cinema_collections_worker.jobs import JobRecord, JobWorker
    from cinema_collections_worker.profile_validation import ProcessingProfile

    job = JobRecord(
        id="job-zero-tail",
        collection_id="films",
        clip_id="clip-zero-tail",
        source_relative_path="films/source.mp4",
        output_relative_path="films/result.mp4",
        source_fingerprint="source",
        profile_fingerprint="profile",
        profile_settings=ProcessingProfile().model_dump(mode="json"),
        duration_seconds=7,
        lead_in_duration_seconds=0,
        tail_out_duration_seconds=0,
    )
    probe = MediaProbeResult(
        valid=True,
        duration_seconds=7.25,
        video_duration_seconds=7,
        audio_duration_seconds=7.25,
    )

    metadata = JobWorker._compiled_timing_metadata(job, ProcessingProfile(), probe)

    assert metadata["content_end_offset_seconds"] == 7.25
    assert metadata["content_duration_seconds"] == 7.25
    assert metadata["tail_out_duration_seconds"] == 0
