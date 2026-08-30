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
        "minimum_segment_duration_seconds": None,
    }
    settings.update(overrides)
    return settings


def _resolve_default(key: object) -> object:
    """Return a voluptuous marker default, resolving lazy lambdas."""
    default = getattr(key, "default", None)
    return default() if callable(default) else default


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


async def _create_profile(hass, aioclient_mock, worker_health_payload, data):
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.post("http://worker.local/api/v1/profiles", json={"revision": 1})
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "user"},
        data=data,
    )
    assert result["type"] == "create_entry"
    return entry, result


@pytest.mark.asyncio
async def test_profile_flow_creates_full_nested_worker_payload(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """The flat form reassembles into the exact Worker profile contract."""
    entry, _result = await _create_profile(
        hass, aioclient_mock, worker_health_payload, profile_form()
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
async def test_profile_flow_bitrate_quality_swaps_the_quality_payload(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Bitrate mode must omit crf entirely and carry bitrate_kbps instead."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form(video_quality_mode="bitrate", video_bitrate_kbps=1500),
    )

    quality = _create_payload(aioclient_mock)["settings"]["video"]["quality"]
    assert quality == {"mode": "bitrate", "bitrate_kbps": 1500}
    assert "crf" not in quality
    assert "bitrate_kbps" not in expected_settings()["video"]["quality"]


@pytest.mark.asyncio
async def test_profile_flow_crop_scaling_omits_sar_fields(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """CropScaling forbids extra keys, so the payload must drop SAR entirely."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form(video_scaling_strategy="crop", video_width=1920, video_height=1080),
    )

    scaling = _create_payload(aioclient_mock)["settings"]["video"]["scaling"]
    assert scaling == {"strategy": "crop", "width": 1920, "height": 1080}
    assert "sar_num" not in scaling
    assert "sar_den" not in scaling


@pytest.mark.asyncio
async def test_profile_flow_disabled_loudness_drops_analysis_targets(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """A disabled loudness profile must not carry two-pass analysis targets."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form(loudness_mode="disabled", loudness_final_mix_normalization=False),
    )

    loudness = _create_payload(aioclient_mock)["settings"]["loudness"]
    assert loudness == {"mode": "disabled", "final_mix_normalization": False}
    assert "integrated_lufs" not in loudness
    assert "true_peak_dbtp" not in loudness
    assert "lra_lu" not in loudness


@pytest.mark.asyncio
async def test_profile_flow_rejects_required_audio_with_silence_fallback(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """The Worker forbids that combination, so the form must reject it locally."""
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.get("http://worker.local/api/v1/assets", json=[])

    shown = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "user"},
        data=profile_form(audio_missing_policy="required", audio_fallback="silence"),
    )

    assert shown["type"] == "form"
    assert "base" in shown["errors"]


@pytest.mark.asyncio
async def test_profile_flow_reconfigure_round_trips_stored_settings(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Reconfiguring with unchanged fields must patch back the stored settings."""
    entry, _result = await _create_profile(
        hass, aioclient_mock, worker_health_payload, profile_form()
    )
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_PROFILE
    )
    aioclient_mock.patch("http://worker.local/api/v1/profiles/4k", json={"revision": 2})

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
        data=profile_form(),
    )

    assert result["type"] == "abort"
    assert _patch_payload(aioclient_mock)["settings"] == expected_settings()


@pytest.mark.asyncio
async def test_profile_flow_reconfigure_prefills_fields_from_stored_settings(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """The shown reconfigure form exposes every field pre-filled from settings."""
    entry, _result = await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form(video_width=1920, video_height=1080, loudness_mode="disabled"),
    )
    subentry = next(
        item for item in entry.subentries.values() if item.subentry_type == SUBENTRY_PROFILE
    )
    aioclient_mock.get("http://worker.local/api/v1/assets", json=[])

    shown = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )

    assert shown["type"] == "form"
    defaults = {str(key.schema): _resolve_default(key) for key in shown["data_schema"].schema}
    assert defaults["video_width"] == 1920
    assert defaults["video_height"] == 1080
    assert defaults["loudness_mode"] == "disabled"
    assert defaults["video_quality_mode"] == "crf"
    assert defaults["audio_missing_policy"] == "required"
    assert defaults["fade_out_seconds"] == 1.5
    assert defaults["intro_reference"] == ""


@pytest.mark.asyncio
async def test_profile_flow_blank_optional_references_serialize_to_null(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Blank optional inputs become null, never empty strings, in the payload."""
    await _create_profile(hass, aioclient_mock, worker_health_payload, profile_form())

    settings = _create_payload(aioclient_mock)["settings"]
    assert settings["intro_reference"] is None
    assert settings["outro_reference"] is None
    assert settings["minimum_segment_duration_seconds"] is None


@pytest.mark.asyncio
async def test_profile_flow_optional_reference_values_are_preserved(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Non-blank optional inputs carry through into the reassembled payload."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form(
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
async def test_profile_form_offers_worker_assets_as_intro_outro_selects(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Uploaded Worker assets become dropdown choices, with None still explicit."""
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.get(
        "http://worker.local/api/v1/assets",
        json=["intro.mp4", "outro.mp4"],
    )

    shown = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "user"},
    )

    assert shown["type"] == "form"
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
async def test_profile_form_falls_back_to_free_text_when_assets_are_unreachable(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """A transient Worker outage must not block opening the profile form."""
    entry = await _pair(hass, aioclient_mock, worker_health_payload)
    aioclient_mock.get(
        "http://worker.local/api/v1/assets",
        exc=TimeoutError(),
    )

    shown = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "user"},
    )

    assert shown["type"] == "form"
    for field_name in ("intro_reference", "outro_reference"):
        field = next(key for key in shown["data_schema"].schema if str(key) == field_name)
        assert shown["data_schema"].schema[field] is str


@pytest.mark.asyncio
async def test_profile_flow_selected_asset_filename_serializes_as_that_string(
    hass, aioclient_mock, worker_health_payload
) -> None:
    """Choosing a dropdown filename carries it through unchanged; None stays null."""
    await _create_profile(
        hass,
        aioclient_mock,
        worker_health_payload,
        profile_form(intro_reference="intro.mp4", outro_reference=""),
    )

    settings = _create_payload(aioclient_mock)["settings"]
    assert settings["intro_reference"] == "intro.mp4"
    assert settings["outro_reference"] is None


def test_profile_schema_is_serializable_for_the_frontend() -> None:
    """The real HTTP layer serializes every flow schema with voluptuous_serialize
    before it ever reaches Python-level form-submission tests. A validator
    construct that reaches Python fine but that serializer can't convert (e.g.
    vol.Any(<validator>, "") — it only recognizes vol.Any(None, X) as
    "optional") surfaces as a bare 500 in a real Home Assistant instance, not
    as a caught flow error; none of the submission-based tests above exercise
    this layer at all.
    """
    import voluptuous_serialize
    from homeassistant.helpers import config_validation as cv

    from custom_components.cinema_collections.subentries import _profile_schema

    for existing in (None, ProfileSubentryData(profile_id="4k", name="4K", settings={})):
        for assets in ((), ("intro.mp4", "outro.mp4")):
            fields = voluptuous_serialize.convert(
                _profile_schema(existing, assets), custom_serializer=cv.custom_serializer
            )
            assert {field["name"] for field in fields} >= {
                "video_bitrate_kbps",
                "minimum_segment_duration_seconds",
                "intro_reference",
                "outro_reference",
            }
