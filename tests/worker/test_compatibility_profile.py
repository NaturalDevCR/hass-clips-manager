from cinema_collections_worker.default_profiles import compatibility_4k_loudness_profile
from cinema_collections_worker.profile_validation import AssetFingerprints, profile_fingerprint


def test_compatibility_profile_uses_approved_baseline():
    profile = compatibility_4k_loudness_profile()
    assert (profile.video.width, profile.video.height, profile.video.fps) == (3840, 2160, 24)
    assert profile.video.codec == "libx264"
    assert profile.video.quality.mode == "crf"
    assert profile.video.quality.crf == 23
    assert profile.audio.codec == "aac"
    assert profile.audio.bitrate_kbps == 192
    assert profile.loudness.integrated_lufs == -18
    assert profile.hardware_acceleration is False
    assert profile.decode_error_policy == "warn"


def test_profile_fingerprint_is_stable_and_asset_sensitive():
    profile = compatibility_4k_loudness_profile()
    assets = AssetFingerprints(intro="a", outro="b")
    assert profile_fingerprint(profile, assets) == profile_fingerprint(profile, assets)
    assert profile_fingerprint(profile, assets) != profile_fingerprint(
        profile, AssetFingerprints(intro="x", outro="b")
    )


def test_profile_fingerprint_accepts_asset_mapping():
    profile = compatibility_4k_loudness_profile()
    assets = {"intro_fingerprint": "a", "outro_fingerprint": "b"}
    assert profile_fingerprint(profile, assets) == profile_fingerprint(
        profile, AssetFingerprints(intro="a", outro="b")
    )
