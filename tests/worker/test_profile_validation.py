import pytest
from cinema_collections_worker.models import ProcessingProfile
from cinema_collections_worker.profile_validation import (
    AudioSettings,
    RequiredAudio,
    validate_profile,
)


def test_invalid_dimensions_are_rejected():
    with pytest.raises(ValueError):
        validate_profile(ProcessingProfile(video={"width": 0}))


def test_crf_and_bitrate_conflict_is_rejected():
    with pytest.raises(ValueError):
        validate_profile(
            ProcessingProfile(
                video={"quality": {"mode": "crf", "crf": 23, "bitrate_kbps": 1000}}
            )
        )


def test_unsafe_extension_is_rejected():
    with pytest.raises(ValueError):
        validate_profile(ProcessingProfile(output={"extension": "../movie"}))


def test_unsupported_transition_is_rejected():
    with pytest.raises(ValueError):
        validate_profile(ProcessingProfile(transitions=[{"type": "wipe", "duration_seconds": 1}]))


def test_invalid_lufs_target_is_rejected():
    with pytest.raises(ValueError):
        validate_profile(ProcessingProfile(loudness={"mode": "two_pass", "integrated_lufs": 2}))


def test_audio_policy_conflict_is_rejected():
    settings = AudioSettings.model_construct(missing_policy=RequiredAudio(), fallback="silence")
    with pytest.raises(ValueError, match="required audio"):
        settings.validate_policy()


def test_fades_must_fit_minimum_segment_duration():
    with pytest.raises(ValueError, match="minimum segment duration"):
        validate_profile(
            ProcessingProfile(
                fade_in_seconds=1,
                fade_out_seconds=1.5,
                minimum_segment_duration_seconds=2,
            )
        )


@pytest.mark.parametrize("reference", ["/tmp/intro.mp4", "../intro.mp4", "assets/../intro.mp4"])
def test_asset_references_reject_absolute_and_traversal_paths(reference):
    with pytest.raises(ValueError):
        ProcessingProfile(intro_reference=reference)


def test_asset_reference_accepts_safe_relative_path():
    assert (
        ProcessingProfile(intro_reference="intros/intro.mp4").intro_reference
        == "intros/intro.mp4"
    )


def test_hardware_acceleration_is_disabled_by_default():
    assert validate_profile(ProcessingProfile()).hardware_acceleration is False
