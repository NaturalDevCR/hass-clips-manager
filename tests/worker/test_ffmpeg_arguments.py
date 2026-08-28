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
