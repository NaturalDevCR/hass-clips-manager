# ruff: noqa: E501

from cinema_collections_worker.ffmpeg import FfmpegCommandBuilder
from cinema_collections_worker.jobs import JobRecord, JobState
from cinema_collections_worker.profile_validation import ProcessingProfile


def test_builder_uses_argument_vector_and_validated_graph_only(tmp_path):
    profile = ProcessingProfile(
        intro_reference="intros/opening.mp4",
        outro_reference="outros/end.mp4",
        loudness={"mode": "two_pass", "integrated_lufs": -18, "true_peak_dbtp": -1.5, "lra_lu": 11},
    )
    job = JobRecord(
        id="job-1",
        kind="compile",
        state=JobState.QUEUED,
        collection_id="films",
        clip_id="clip-1",
        source_relative_path="films/source;still-a-file.mp4",
        output_relative_path="films/result.mp4",
        source_fingerprint="source",
        profile_fingerprint="profile",
        profile_settings=profile.model_dump(mode="json"),
        duration_seconds=10,
        source_path=tmp_path / "source;still-a-file.mp4",
        temporary_output_path=tmp_path / "tmp" / "out.mp4",
        intro_path=tmp_path / "opening.mp4",
        outro_path=tmp_path / "end.mp4",
    )

    command = FfmpegCommandBuilder().build(job)

    assert command[0] == "ffmpeg"
    assert "shell" not in " ".join(command)
    assert str(job.source_path) in command
    assert any("loudnorm=I=-18" in item for item in command)
    graph = command[command.index("-filter_complex") + 1]
    assert "xfade" in graph and "acrossfade" in graph
    assert "user_filter" not in graph
    assert command[-1] == str(job.temporary_output_path)


def test_builder_input_indices_and_transition_offsets_follow_segment_durations(tmp_path):
    profile = ProcessingProfile(intro_reference="intro.mp4", outro_reference="outro.mp4")
    job = JobRecord(
        id="job-2",
        kind="compile",
        state=JobState.QUEUED,
        collection_id="films",
        clip_id="clip-2",
        source_relative_path="films/source.mp4",
        output_relative_path="films/result.mp4",
        source_fingerprint="source",
        profile_fingerprint="profile",
        profile_settings=profile.model_dump(mode="json"),
        duration_seconds=10,
        intro_duration_seconds=4,
        outro_duration_seconds=3,
        source_path=tmp_path / "clip.mp4",
        temporary_output_path=tmp_path / "out.mp4",
        intro_path=tmp_path / "intro.mp4",
        outro_path=tmp_path / "outro.mp4",
    )

    command = FfmpegCommandBuilder().build(job)
    graph = command[command.index("-filter_complex") + 1]

    assert command[command.index("-i") + 1] == str(job.source_path)
    assert graph.index("[0:v]") < graph.index("[1:v]") < graph.index("[2:v]")
    assert "[v_intro][v_clip]xfade=transition=fade:duration=1:offset=3" in graph
    assert "[v_intro_clip][v_outro]xfade=transition=fade:duration=1:offset=12" in graph
    assert "[1:a]aformat" in graph and "atrim=duration=4.000" in graph
    assert "[2:a]aformat" in graph and "atrim=duration=3.000" in graph
    assert "fade=t=out:st=13.5:d=1.5" in graph


def test_builder_without_rate_control_or_keyframe_fields_returns_the_legacy_argv(tmp_path):
    """Unset optional encoder fields must not change the command by one byte."""
    profile = ProcessingProfile()
    job = JobRecord(
        id="job-4",
        kind="compile",
        state=JobState.QUEUED,
        collection_id="films",
        clip_id="clip-4",
        source_relative_path="films/source.mp4",
        output_relative_path="films/result.mp4",
        source_fingerprint="source",
        profile_fingerprint="profile",
        profile_settings=profile.model_dump(mode="json"),
        duration_seconds=10,
        source_path=tmp_path / "source.mp4",
        temporary_output_path=tmp_path / "out.mp4",
    )

    command = FfmpegCommandBuilder().build(job)

    graph = (
        "[0:v]scale=3840:2160:force_original_aspect_ratio=decrease,"
        "pad=3840:2160:(ow-iw)/2:(oh-ih)/2,setsar=1/1,fps=24,format=yuv420p[v_clip];"
        "[0:a]aformat=channel_layouts=stereo,aresample=48000,apad,atrim=duration=10.000,"
        "loudnorm=I=-18:TP=-1.5:LRA=11[a_clip];"
        "[v_clip]fade=t=in:st=0:d=1,fade=t=out:st=8.5:d=1.5[v_final];"
        "[a_clip]loudnorm=I=-18:TP=-1.5:LRA=11,anull[a_final]"
    )
    assert command == [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-i",
        str(job.source_path),
        "-filter_complex",
        graph,
        "-map",
        "[v_final]",
        "-map",
        "[a_final]",
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-profile:v",
        "high",
        "-level:v",
        "5.1",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(job.temporary_output_path),
    ]


def test_builder_emits_maxrate_bufsize_and_keyframe_interval_when_configured(tmp_path):
    profile = ProcessingProfile(
        video={
            "maxrate_kbps": 8000,
            "bufsize_kbps": 16000,
            "keyframe_interval_seconds": 2.0,
        }
    )
    job = JobRecord(
        id="job-5",
        kind="compile",
        state=JobState.QUEUED,
        collection_id="films",
        clip_id="clip-5",
        source_relative_path="films/source.mp4",
        output_relative_path="films/result.mp4",
        source_fingerprint="source",
        profile_fingerprint="profile",
        profile_settings=profile.model_dump(mode="json"),
        duration_seconds=10,
        source_path=tmp_path / "source.mp4",
        temporary_output_path=tmp_path / "out.mp4",
    )

    command = FfmpegCommandBuilder().build(job)

    assert command[command.index("-maxrate") + 1] == "8000k"
    assert command[command.index("-bufsize") + 1] == "16000k"
    # 2.0 seconds at 24 fps rounds to a 48-frame keyframe interval.
    assert command[command.index("-g") + 1] == "48"


def test_builder_honors_h264_profile_level_and_decode_failure_policy(tmp_path):
    profile = ProcessingProfile(decode_error_policy="fail")
    job = JobRecord(
        id="job-3",
        collection_id="films",
        clip_id="clip-3",
        source_relative_path="films/source.mp4",
        output_relative_path="films/result.mp4",
        source_fingerprint="source",
        profile_fingerprint="profile",
        profile_settings=profile.model_dump(mode="json"),
        duration_seconds=10,
        source_path=tmp_path / "source.mp4",
        temporary_output_path=tmp_path / "result.mp4",
    )

    command = FfmpegCommandBuilder().build(job)

    assert command[command.index("-profile:v") + 1] == "high"
    assert command[command.index("-level:v") + 1] == "5.1"
    assert "-xerror" in command
