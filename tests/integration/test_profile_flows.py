"""Field-by-field processing-profile subentry flow coverage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from homeassistant.helpers.selector import SelectSelector

from custom_components.cinema_collections.const import (
    CONF_ENDPOINT,
    CONF_TOKEN,
    DOMAIN,
    SUBENTRY_PROFILE,
)
from custom_components.cinema_collections.subentries import ProfileSubentryData

PROFILE_STEPS = ("video", "audio", "timing", "output")

VIDEO_STEP_FIELDS = (
    "profile_id",
    "name",
    "video_width",
    "video_height",
    "video_fps",
    "video_codec",
    "video_preset",
    "video_quality_mode",
    "video_crf",
    "video_bitrate_kbps",
    "video_h264_profile",
    "video_level",
    "video_pixel_format",
    "video_scaling_strategy",
    "video_sar_num",
    "video_sar_den",
    "video_fast_start",
    "video_maxrate_kbps",
    "video_bufsize_kbps",
    "video_keyframe_interval_seconds",
)
AUDIO_STEP_FIELDS = (
    "audio_codec",
    "audio_bitrate_kbps",
    "audio_channels",
    "audio_sample_rate",
    "audio_missing_policy",
    "audio_fallback",
    "audio_pad_or_trim",
    "loudness_mode",
    "loudness_integrated_lufs",
    "loudness_true_peak_dbtp",
    "loudness_lra_lu",
    "loudness_final_mix_normalization",
)
TIMING_STEP_FIELDS = (
    "intro_reference",
    "outro_reference",
    "intro_to_clip_fade_seconds",
    "clip_to_outro_fade_seconds",
    "fade_in_seconds",
    "fade_out_seconds",
    "minimum_segment_duration_seconds",
)
OUTPUT_STEP_FIELDS = (
    "output_container",
    "hardware_acceleration",
    "decode_error_policy",
    "timeout_seconds",
    "timeout_seconds_per_minute",
)
STEP_FIELDS = (VIDEO_STEP_FIELDS, AUDIO_STEP_FIELDS, TIMING_STEP_FIELDS, OUTPUT_STEP_FIELDS)


def profile_form(**overrides: object) -> dict[str, object]:
    """Build the full flat field set for the profile subentry form."""
    values: dict[str, object] = {
        "profile_id": "4k",
        "name": "Cinema 4K",
        "video_width": 3840,
        "video_height": 2160,
        "video_fps": 24,
        "video_codec": "libx264",
        "video_preset": "fast",
        "video_quality_mode": "crf",
        "video_crf": 23.0,
        "video_bitrate_kbps": "",
        "video_h264_profile": "high",
        "video_level": "5.1",
        "video_pixel_format": "yuv420p",
        "video_scaling_strategy": "aspect_fit",
        "video_sar_num": 1,
        "video_sar_den": 1,
        "video_fast_start": True,
        "video_maxrate_kbps": "",
        "video_bufsize_kbps": "",
        "video_keyframe_interval_seconds": "",
        "audio_codec": "aac",
        "audio_bitrate_kbps": 192,
        "audio_channels": 2,
        "audio_sample_rate": 48000,
        "audio_missing_policy": "required",
        "audio_fallback": "none",
        "audio_pad_or_trim": True,
        "loudness_mode": "two_pass",
        "loudness_integrated_lufs": -18.0,
        "loudness_true_peak_dbtp": -1.5,
        "loudness_lra_lu": 11.0,
        "loudness_final_mix_normalization": True,
        "intro_to_clip_fade_seconds": 1.0,
        "clip_to_outro_fade_seconds": 1.0,
        "fade_in_seconds": 1.0,
        "fade_out_seconds": 1.5,
        "output_container": "mp4",
        "hardware_acceleration": False,
        "decode_error_policy": "warn",
        "intro_reference": "",
        "outro_reference": "",
        "timeout_seconds": 300,
        "timeout_seconds_per_minute": 120,
        "minimum_segment_duration_seconds": "",
    }
    values.update(overrides)
    return values


def expected_settings(**overrides: object) -> dict[str, object]:
    """Return the exact nested Worker payload for the default profile form."""
    settings: dict[str, object] = {
        "video": {
            "width": 3840,
            "height": 2160,
            "fps": 24,
            "codec": "libx264",
            "preset": "fast",
            "quality": {"mode": "crf", "crf": 23.0},
            "h264_profile": "high",
            "level": "5.1",
            "pixel_format": "yuv420p",
            "scaling": {
                "strategy": "aspect_fit",
                "width": 3840,
                "height": 2160,
                "sar_num": 1,
                "sar_den": 1,
            },
            "fast_start": True,
            "maxrate_kbps": None,
            "bufsize_kbps": None,
            "keyframe_interval_seconds": None,
        },
        "audio": {
            "codec": "aac",
            "bitrate_kbps": 192,
            "channels": 2,
            "sample_rate": 48000,
            "missing_policy": {"mode": "required"},
            "fallback": "none",
            "pad_or_trim": True,
        },
        "loudness": {
            "mode": "two_pass",
            "integrated_lufs": -18.0,
            "true_peak_dbtp": -1.5,
            "lra_lu": 11.0,
            "final_mix_normalization": True,
        },
        "transitions": [
            {
                "type": "fade",
                "duration_seconds": 1.0,
                "from_segment": "intro",
                "to_segment": "clip",
            },
            {
                "type": "fade",
                "duration_seconds": 1.0,
                "from_segment": "clip",
                "to_segment": "outro",
            },
        ],
        "fade_in_seconds": 1.0,
        "fade_out_seconds": 1.5,
        "output": {
            "container": "mp4",
            "extension": "mp4",
            "atomic_finalize": True,
            "temporary_output": True,
        },
        "hardware_acceleration": False,
        "decode_error_policy": "warn",
        "intro_reference": None,
        "outro_reference": None,
        "timeout_seconds": 300,
        "timeout_seconds_per_minute": 120,
        "minimum_segment_duration_seconds": None,
    }
    settings.update(overrides)
    return settings


def profile_form_steps(**overrides: object) -> list[dict[str, object]]:
    """Split the full flat form into one dict per wizard step.

    The dropdown fields submit their string values and the blank-able optional
    fields submit strings, matching what the Home Assistant frontend sends.
    """
    form = profile_form(**overrides)
    steps = [{name: form[name] for name in fields} for fields in STEP_FIELDS]
    video = steps[0]
    video["video_bitrate_kbps"] = str(video["video_bitrate_kbps"])
    video["video_maxrate_kbps"] = str(video["video_maxrate_kbps"])
    video["video_bufsize_kbps"] = str(video["video_bufsize_kbps"])
    video["video_keyframe_interval_seconds"] = str(video["video_keyframe_interval_seconds"])
    audio = steps[1]
    audio["audio_channels"] = str(audio["audio_channels"])
    audio["audio_sample_rate"] = str(audio["audio_sample_rate"])
    timing = steps[2]
    timing["minimum_segment_duration_seconds"] = str(timing["minimum_segment_duration_seconds"])
    return steps


def _resolve_default(key: object) -> object:
    """Return a voluptuous marker default, resolving lazy lambdas."""
    default = getattr(key, "default", None)
    return default() if callable(default) else default


def _field_validators(schema: Any) -> dict[str, Any]:
    """Map each field name to its voluptuous validator from a step schema."""
    return {str(key.schema): schema.schema[key] for key in schema.schema}


async def _pair(hass, aioclient_mock, worker_health_payload):
    aioclient_mock.get("http://worker.local/api/v1/health", json=worker_health_payload)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_ENDPOINT: "http://worker.local", CONF_TOKEN: "pairing-token"},
    )
    assert result["type"] == "create_entry"
    return hass.config_entries.async_entries(DOMAIN)[0]


def _create_payload(aioclient_mock) -> Mapping[str, Any]:
    calls = [
        call
        for call in aioclient_mock.mock_calls
        if str(call[0]).lower() == "post" and str(call[1]) == "http://worker.local/api/v1/profiles"
    ]
    assert calls, "profile create request was not recorded"
    data = calls[-1][2]
    assert isinstance(data, Mapping)
    return data


def _patch_payload(aioclient_mock) -> Mapping[str, Any]:
    calls = [
        call
        for call in aioclient_mock.mock_calls
        if str(call[0]).lower() == "patch"
        and str(call[1]) == "http://worker.local/api/v1/profiles/4k"
    ]
    assert calls, "profile patch request was not recorded"
    data = calls[-1][2]
    assert isinstance(data, Mapping)
    return data


async def _start_profile_flow(hass, entry, source, subentry_id=None, data=None):
    context = {"source": source}
    if subentry_id is not None:
        context["subentry_id"] = subentry_id
    kwargs = {"context": context}
    if data is not None:
        kwargs["data"] = data
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE), **kwargs
    )


async def _configure_flow(hass, result, data):
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


async def _create_profile(hass, aioclient_mock, worker_health_payload, data_steps):
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.post("http://worker.local/api/v1/profiles", json={"revision": 1})
    aioclient_mock.get("http://worker.local/api/v1/assets", json=[])
    result = await _start_profile_flow(hass, entry, "user", data=data_steps[0])
    for step_data in data_steps[1:]:
        result = await _configure_flow(hass, result, step_data)
    assert result["type"] == "create_entry"
    return entry, result


async def _walk_reconfigure(hass, entry, subentry_id):
    """Start a reconfigure flow and submit each step's pre-filled defaults."""
    result = await _start_profile_flow(hass, entry, "reconfigure", subentry_id)
    for _ in PROFILE_STEPS:
        assert result["type"] == "form"
        defaults = {str(key.schema): _resolve_default(key) for key in result["data_schema"].schema}
        result = await _configure_flow(hass, result, defaults)
    return result


@pytest.mark.asyncio
async def test_profile_wizard_walking_all_steps_produces_the_same_worker_payload(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """The four-step wizard reassembles into the exact Worker profile contract."""
    entry, _result = await _create_profile(
        hass, aioclient_mock, worker_health_payload, profile_form_steps()
    )

    payload = _create_payload(aioclient_mock)
    assert payload["id"] == "4k"
    assert payload["name"] == "Cinema 4K"
    assert payload["settings"] == expected_settings()
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_PROFILE
    )
    assert subentry.data["worker_revision"] == 1


@pytest.mark.asyncio
async def test_profile_wizard_bitrate_quality_swaps_the_quality_payload(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Bitrate mode must omit crf entirely and carry bitrate_kbps instead."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(video_quality_mode="bitrate", video_bitrate_kbps=1500),
    )

    quality = _create_payload(aioclient_mock)["settings"]["video"]["quality"]
    assert quality == {"mode": "bitrate", "bitrate_kbps": 1500}
    assert "crf" not in quality
    assert "bitrate_kbps" not in expected_settings()["video"]["quality"]


@pytest.mark.asyncio
async def test_profile_wizard_crop_scaling_omits_sar_fields(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """CropScaling forbids extra keys, so the payload must drop SAR entirely."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(video_scaling_strategy="crop", video_width=1920, video_height=1080),
    )

    scaling = _create_payload(aioclient_mock)["settings"]["video"]["scaling"]
    assert scaling == {"strategy": "crop", "width": 1920, "height": 1080}
    assert "sar_num" not in scaling
    assert "sar_den" not in scaling


@pytest.mark.asyncio
async def test_profile_wizard_disabled_loudness_drops_analysis_targets(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """A disabled loudness profile must not carry two-pass analysis targets."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(loudness_mode="disabled", loudness_final_mix_normalization=False),
    )

    loudness = _create_payload(aioclient_mock)["settings"]["loudness"]
    assert loudness == {"mode": "disabled", "final_mix_normalization": False}
    assert "integrated_lufs" not in loudness
    assert "true_peak_dbtp" not in loudness
    assert "lra_lu" not in loudness


@pytest.mark.asyncio
async def test_profile_wizard_rate_control_fields_reach_the_video_payload(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Bitrate ceiling, buffer and keyframe interval flow into video settings."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(
            video_maxrate_kbps=8000,
            video_bufsize_kbps=16000,
            video_keyframe_interval_seconds=2.0,
            timeout_seconds_per_minute=180,
        ),
    )

    settings = _create_payload(aioclient_mock)["settings"]
    video = settings["video"]
    assert video["maxrate_kbps"] == 8000
    assert video["bufsize_kbps"] == 16000
    assert video["keyframe_interval_seconds"] == 2.0
    assert settings["timeout_seconds_per_minute"] == 180


@pytest.mark.asyncio
async def test_profile_wizard_blank_rate_control_fields_serialize_to_null(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Blank optional encoder fields become null, never empty strings."""
    await _create_profile(hass, aioclient_mock, worker_health_payload, profile_form_steps())

    video = _create_payload(aioclient_mock)["settings"]["video"]
    assert video["maxrate_kbps"] is None
    assert video["bufsize_kbps"] is None
    assert video["keyframe_interval_seconds"] is None


@pytest.mark.asyncio
async def test_profile_wizard_rejects_required_audio_with_silence_fallback_on_audio_step(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """The forbidden combination surfaces on the Audio step and does not advance."""
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.get("http://worker.local/api/v1/assets", json=[])
    steps = profile_form_steps(audio_missing_policy="required", audio_fallback="silence")

    shown = await _start_profile_flow(hass, entry, "user")
    assert shown["step_id"] == "video"
    shown = await _configure_flow(hass, shown, steps[0])
    assert shown["step_id"] == "audio"
    shown = await _configure_flow(hass, shown, steps[1])

    assert shown["type"] == "form"
    assert shown["step_id"] == "audio"
    assert shown["errors"] == {"base": "profile_audio_policy"}


@pytest.mark.asyncio
async def test_profile_wizard_rejects_bitrate_mode_without_a_bitrate_on_video_step(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Bitrate quality requires a bitrate, reported on the Video step."""
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    steps = profile_form_steps(video_quality_mode="bitrate", video_bitrate_kbps="")

    shown = await _start_profile_flow(hass, entry, "user", data=steps[0])

    assert shown["type"] == "form"
    assert shown["step_id"] == "video"
    assert shown["errors"] == {"base": "invalid_profile"}


@pytest.mark.asyncio
async def test_profile_wizard_reconfigure_round_trips_stored_settings(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Reconfiguring with unchanged fields must patch back the stored settings."""
    entry, _result = await _create_profile(
        hass, aioclient_mock, worker_health_payload, profile_form_steps()
    )
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_PROFILE
    )
    aioclient_mock.patch("http://worker.local/api/v1/profiles/4k", json={"revision": 2})

    result = await _walk_reconfigure(hass, entry, subentry.subentry_id)

    assert result["type"] == "abort"
    assert _patch_payload(aioclient_mock)["settings"] == expected_settings()


@pytest.mark.asyncio
async def test_profile_wizard_reconfigure_prefills_every_step_from_stored_settings(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Each reconfigure step exposes its fields pre-filled from stored settings."""
    entry, _result = await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(
            video_width=1920,
            video_height=1080,
            loudness_mode="disabled",
            intro_reference="intros/intro.mp4",
            fade_out_seconds=2.5,
        ),
    )
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_PROFILE
    )
    aioclient_mock.get("http://worker.local/api/v1/assets", json=[])
    aioclient_mock.patch("http://worker.local/api/v1/profiles/4k", json={"revision": 2})

    shown = await _start_profile_flow(hass, entry, "reconfigure", subentry.subentry_id)
    for step_id, step_data in zip(PROFILE_STEPS, profile_form_steps(), strict=True):
        assert shown["type"] == "form"
        assert shown["step_id"] == step_id
        defaults = {str(key.schema): _resolve_default(key) for key in shown["data_schema"].schema}
        if step_id == "video":
            assert defaults["profile_id"] == "4k"
            assert defaults["name"] == "Cinema 4K"
            assert defaults["video_width"] == 1920
            assert defaults["video_height"] == 1080
            assert defaults["video_quality_mode"] == "crf"
        if step_id == "audio":
            assert defaults["loudness_mode"] == "disabled"
            assert defaults["audio_missing_policy"] == "required"
            assert defaults["audio_channels"] == "2"
        if step_id == "timing":
            assert defaults["intro_reference"] == "intros/intro.mp4"
            assert defaults["fade_out_seconds"] == 2.5
        shown = await _configure_flow(hass, shown, step_data)


@pytest.mark.asyncio
async def test_profile_wizard_blank_optional_references_serialize_to_null(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Blank optional inputs become null, never empty strings, in the payload."""
    await _create_profile(hass, aioclient_mock, worker_health_payload, profile_form_steps())

    settings = _create_payload(aioclient_mock)["settings"]
    assert settings["intro_reference"] is None
    assert settings["outro_reference"] is None
    assert settings["minimum_segment_duration_seconds"] is None


@pytest.mark.asyncio
async def test_profile_wizard_optional_reference_values_are_preserved(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Non-blank optional inputs carry through into the reassembled payload."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(
            intro_reference="intros/intro.mp4",
            outro_reference="outros/outro.mp4",
            minimum_segment_duration_seconds=5.0,
        ),
    )

    settings = _create_payload(aioclient_mock)["settings"]
    assert settings["intro_reference"] == "intros/intro.mp4"
    assert settings["outro_reference"] == "outros/outro.mp4"
    assert settings["minimum_segment_duration_seconds"] == 5.0


@pytest.mark.asyncio
async def test_profile_wizard_offers_worker_assets_as_intro_outro_selects(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Uploaded Worker assets become dropdown choices on the timing step."""
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.get(
        "http://worker.local/api/v1/assets",
        json=["intro.mp4", "outro.mp4"],
    )
    steps = profile_form_steps()

    shown = await _start_profile_flow(hass, entry, "user", data=steps[0])
    shown = await _configure_flow(hass, shown, steps[1])

    assert shown["type"] == "form"
    assert shown["step_id"] == "timing"
    for field_name in ("intro_reference", "outro_reference"):
        field = next(key for key in shown["data_schema"].schema if str(key) == field_name)
        reference_selector = shown["data_schema"].schema[field]
        assert isinstance(reference_selector, SelectSelector)
        assert reference_selector.config["custom_value"] is True
        assert reference_selector.config["options"] == [
            {"value": "", "label": "None"},
            {"value": "intro.mp4", "label": "intro.mp4"},
            {"value": "outro.mp4", "label": "outro.mp4"},
        ]


@pytest.mark.asyncio
async def test_profile_wizard_falls_back_to_free_text_when_assets_are_unreachable(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """A transient Worker outage must not block walking to the timing step."""
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.get(
        "http://worker.local/api/v1/assets",
        exc=TimeoutError(),
    )
    steps = profile_form_steps()

    shown = await _start_profile_flow(hass, entry, "user", data=steps[0])
    shown = await _configure_flow(hass, shown, steps[1])

    assert shown["type"] == "form"
    assert shown["step_id"] == "timing"
    for field_name in ("intro_reference", "outro_reference"):
        field = next(key for key in shown["data_schema"].schema if str(key) == field_name)
        assert shown["data_schema"].schema[field] is str


@pytest.mark.asyncio
async def test_profile_wizard_selected_asset_filename_serializes_as_that_string(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Choosing a dropdown filename carries it through unchanged; None stays null."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(intro_reference="intro.mp4", outro_reference=""),
    )

    settings = _create_payload(aioclient_mock)["settings"]
    assert settings["intro_reference"] == "intro.mp4"
    assert settings["outro_reference"] is None


@pytest.mark.asyncio
async def test_profile_wizard_audio_numbers_reach_the_payload_as_ints(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """The labelled audio dropdowns must not turn integers into strings."""
    await _create_profile(hass, aioclient_mock, worker_health_payload, profile_form_steps())

    audio = _create_payload(aioclient_mock)["settings"]["audio"]
    assert isinstance(audio["channels"], int) and audio["channels"] == 2
    assert isinstance(audio["sample_rate"], int) and audio["sample_rate"] == 48000


@pytest.mark.asyncio
async def test_profile_wizard_custom_dropdown_values_flow_through_as_typed(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """custom_value dropdowns still accept values outside the offered lists."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form_steps(video_codec="libx265", audio_channels="7", audio_sample_rate="96000"),
    )

    settings = _create_payload(aioclient_mock)["settings"]
    assert settings["video"]["codec"] == "libx265"
    assert settings["audio"]["channels"] == 7
    assert settings["audio"]["sample_rate"] == 96000


def test_profile_wizard_steps_split_the_form_fields() -> None:
    """Each wizard step owns a disjoint slice of the flat field set."""
    from custom_components.cinema_collections.subentries import (
        _profile_audio_schema,
        _profile_output_schema,
        _profile_timing_schema,
        _profile_video_schema,
    )

    schemas = {
        "video": _profile_video_schema(None),
        "audio": _profile_audio_schema(None),
        "timing": _profile_timing_schema(None),
        "output": _profile_output_schema(None),
    }
    expected = {
        "video": set(VIDEO_STEP_FIELDS),
        "audio": set(AUDIO_STEP_FIELDS),
        "timing": set(TIMING_STEP_FIELDS),
        "output": set(OUTPUT_STEP_FIELDS),
    }
    for step_id, schema in schemas.items():
        fields = {str(key.schema) for key in schema.schema}
        assert fields == expected[step_id]
    assert set().union(*expected.values()) == set(profile_form())


def test_profile_wizard_video_dropdowns_offer_the_expected_options() -> None:
    """Codec, preset, H.264 profile, level and pixel format expose stable options."""
    from custom_components.cinema_collections.subentries import _profile_video_schema

    video = _field_validators(_profile_video_schema(None))

    codec = video["video_codec"]
    assert isinstance(codec, SelectSelector)
    assert codec.config["custom_value"] is True
    assert codec.config["options"] == [
        {"value": "libx264", "label": "libx264"},
        {"value": "libx265", "label": "libx265"},
    ]

    preset = video["video_preset"]
    assert isinstance(preset, SelectSelector)
    assert preset.config["custom_value"] is True
    assert preset.config["options"] == [
        {"value": value, "label": value}
        for value in (
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
            "placebo",
        )
    ]

    profile = video["video_h264_profile"]
    assert isinstance(profile, SelectSelector)
    assert profile.config["custom_value"] is True
    assert profile.config["options"] == [
        {"value": value, "label": value}
        for value in ("baseline", "main", "high", "high10", "high422", "high444")
    ]

    level = video["video_level"]
    assert isinstance(level, SelectSelector)
    assert level.config["custom_value"] is True
    assert level.config["options"] == [
        {"value": value, "label": value}
        for value in ("3.0", "3.1", "4.0", "4.1", "4.2", "5.0", "5.1", "5.2", "6.0", "6.1", "6.2")
    ]

    pixel_format = video["video_pixel_format"]
    assert isinstance(pixel_format, SelectSelector)
    assert pixel_format.config["custom_value"] is True
    assert pixel_format.config["options"] == [
        {"value": value, "label": value}
        for value in ("yuv420p", "yuv422p", "yuv444p", "yuv420p10le")
    ]


def test_profile_wizard_audio_dropdowns_offer_the_expected_options() -> None:
    """Codec, channel count and sample rate expose labelled stable options."""
    from custom_components.cinema_collections.subentries import _profile_audio_schema

    audio = _field_validators(_profile_audio_schema(None))

    codec = audio["audio_codec"]
    assert isinstance(codec, SelectSelector)
    assert codec.config["custom_value"] is True
    assert codec.config["options"] == [
        {"value": "aac", "label": "aac"},
        {"value": "libopus", "label": "libopus"},
        {"value": "libmp3lame", "label": "libmp3lame"},
        {"value": "flac", "label": "flac"},
    ]

    channels = audio["audio_channels"]
    assert isinstance(channels, SelectSelector)
    assert channels.config["custom_value"] is True
    assert channels.config["options"] == [
        {"value": "1", "label": "Mono"},
        {"value": "2", "label": "Stereo"},
        {"value": "6", "label": "5.1"},
        {"value": "8", "label": "7.1"},
    ]

    sample_rate = audio["audio_sample_rate"]
    assert isinstance(sample_rate, SelectSelector)
    assert sample_rate.config["custom_value"] is True
    assert sample_rate.config["options"] == [
        {"value": "44100", "label": "44100"},
        {"value": "48000", "label": "48000"},
        {"value": "96000", "label": "96000"},
    ]


def test_profile_schemas_are_serializable_for_the_frontend() -> None:
    """The real HTTP layer serializes every step schema with voluptuous_serialize
    before it ever reaches Python-level form-submission tests. A validator
    construct that reaches Python fine but that serializer can't convert (e.g.
    vol.Any(<validator>, "") — it only recognizes vol.Any(None, X) as
    "optional") surfaces as a bare 500 in a real Home Assistant instance, not
    as a caught flow error; none of the submission-based tests above exercise
    this layer at all.
    """
    import voluptuous_serialize
    from homeassistant.helpers import config_validation as cv

    from custom_components.cinema_collections.subentries import (
        _profile_audio_schema,
        _profile_output_schema,
        _profile_timing_schema,
        _profile_video_schema,
    )

    builders = (
        _profile_video_schema,
        _profile_audio_schema,
        _profile_output_schema,
    )
    for existing in (None, ProfileSubentryData(profile_id="4k", name="4K", settings={})):
        for assets in ((), ("intro.mp4", "outro.mp4")):
            seen: set[str] = set()
            for builder in builders:
                fields = voluptuous_serialize.convert(
                    builder(existing), custom_serializer=cv.custom_serializer
                )
                assert isinstance(fields, list) and fields
                seen.update(field["name"] for field in fields)
            timing_fields = voluptuous_serialize.convert(
                _profile_timing_schema(existing, assets), custom_serializer=cv.custom_serializer
            )
            assert isinstance(timing_fields, list) and timing_fields
            seen.update(field["name"] for field in timing_fields)
            assert seen == set(profile_form())
