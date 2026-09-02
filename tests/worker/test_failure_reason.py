# ruff: noqa: E501
"""Failed encodes keep the decisive lines instead of the x264 statistics epilogue."""

import json

from cinema_collections_worker.jobs import CompileRequest, JobState, JobWorker
from cinema_collections_worker.probe import MediaProbeResult
from cinema_collections_worker.profile_validation import ProcessingProfile
from test_queue import _configured_service


class _ValidProbe:
    def probe(self, _path):
        return MediaProbeResult(valid=True, duration_seconds=1)


def _x264_statistics_epilogue() -> str:
    rows = [
        "[libx264 @ 0x7f8b0c00] frame I:240   Avg QP:21.00  size:200000",
        "[libx264 @ 0x7f8b0c00] frame P:3000  Avg QP:24.00  size: 50000",
        "[libx264 @ 0x7f8b0c00] frame B:9000  Avg QP:27.00  size: 10000",
        "[libx264 @ 0x7f8b0c00] consecutive B-frames:  2.0% 10.0% 20.0% 68.0%",
        "[libx264 @ 0x7f8b0c00] mb I  I16..4: 10.0% 80.0% 10.0%",
        "[libx264 @ 0x7f8b0c00] mb P  P16..4:  5.0% 15.0%  2.0%",
        "[libx264 @ 0x7f8b0c00] coded y,uvDC,uvAC intra: 60.0% 70.0% 50.0% inter: 20.0% 30.0% 5.0%",
        "[libx264 @ 0x7f8b0c00] i8 v,h,dc,ddl,ddr,vr,hd,vl,hu: 20% 20% 20% 5% 5% 5% 5% 10% 10%",
        "[libx264 @ 0x7f8b0c00] Weighted P-Frames: Y:5.0% UV:3.0%",
        "[libx264 @ 0x7f8b0c00] kb/s:1234.56",
    ]
    rows += [f"[libx264 @ 0x7f8b0c00] ref P L0: {50 + index}% {index}%" for index in range(120)]
    rows += [f"[aac @ 0x7f8b0d00] Qavg: {100 + index}.5" for index in range(40)]
    return "\n".join(rows)


def test_failed_encode_reports_the_decisive_line_not_the_codec_statistics(tmp_path) -> None:
    """The thriller-mj-edit.mp4 timeout must surface, not the x264 epilogue."""
    db, resolver, service = _configured_service(tmp_path)
    profile = ProcessingProfile(loudness={"mode": "disabled"}).model_dump(mode="json")
    with db.connection:
        db.connection.execute("UPDATE profiles SET settings=?", (json.dumps(profile),))
    service.enqueue_compile(CompileRequest(collection_id="films", max_attempts=1))
    output = (
        _x264_statistics_epilogue()
        + f"\n{resolver.roots['source']}/films/example.mp4 Bearer leaked-token"
        + "\nExiting normally, received signal 15"
        + "\nFFmpeg timed out"
    )

    class StatsEpilogueProcess:
        pid = 0
        returncode = 1

        def communicate(self, timeout):
            return "", output

    result = JobWorker(
        db,
        resolver,
        probe_client=_ValidProbe(),
        process_factory=lambda *_args, **_kwargs: StatsEpilogueProcess(),
    ).run_once()

    assert result is not None and result.job.state is JobState.FAILED
    error = str(result.job.error)
    assert "FFmpeg timed out" in error
    assert "received signal 15" in error
    assert "Avg QP" not in error
    assert "kb/s" not in error
    assert "Qavg" not in error
    assert len(error) <= 1000
    assert str(resolver.roots["source"]) not in error
    assert "leaked-token" not in error
