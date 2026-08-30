"""Config-subentry persistence and Worker revision synchronization."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, cast

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentry,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api_client import WorkerApiClient, WorkerApiError
from .const import CONF_ENDPOINT, CONF_TOKEN, SUBENTRY_COLLECTION, SUBENTRY_PROFILE
from .models import WorkerProfileSummary
from .resolver import CollectionPolicy
from .scheduler import CompilationSchedule, schedules_from_mapping

_COLLECTION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WorkerValidationError(ValueError):
    """A Worker validation or optimistic-concurrency error safe for config flows."""


class ProfileAudioPolicyError(ValueError):
    """A missing-audio policy the Worker forbids, surfaced as a form error."""


def _check_identifier(value: str, label: str) -> str:
    if not _COLLECTION_ID.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase URL-safe slug")
    return value


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise ValueError("schedule window timestamps must be timezone-aware ISO 8601 values")
    return parsed


@dataclass(frozen=True, slots=True)
class CollectionSubentryData:
    """Durable integration configuration for a stable Worker collection ID."""

    collection_id: str
    name: str
    source_directory: str
    processing_profile_id: str
    enabled: bool = True
    priority: int = 0
    starts_at: str | None = None
    ends_at: str | None = None
    is_default: bool = False
    allow_manual_override: bool = True
    tags: tuple[str, ...] = ()
    notes: str | None = None
    schedule: Mapping[str, object] = field(default_factory=lambda: {})
    worker_revision: int | None = None

    def __post_init__(self) -> None:
        _check_identifier(self.collection_id, "collection ID")
        _check_identifier(self.processing_profile_id, "processing profile ID")
        if not self.name.strip() or not self.source_directory.strip():
            raise ValueError("collection name and source directory are required")
        start = _optional_datetime(self.starts_at)
        end = _optional_datetime(self.ends_at)
        if start is not None and end is not None and start > end:
            raise ValueError("schedule window end must not precede its start")
        if self.worker_revision is not None and self.worker_revision < 1:
            raise ValueError("Worker revision must be positive")

    def with_updates(self, **changes: object) -> CollectionSubentryData:
        """Return changed immutable policy data while preserving its stable ID."""
        if "collection_id" in changes and changes["collection_id"] != self.collection_id:
            raise ValueError("collection IDs are immutable")
        return replace(self, **changes)

    def as_dict(self) -> dict[str, object]:
        """Serialize only durable, JSON-compatible config-subentry fields."""
        return {
            "collection_id": self.collection_id,
            "name": self.name,
            "source_directory": self.source_directory,
            "processing_profile_id": self.processing_profile_id,
            "enabled": self.enabled,
            "priority": self.priority,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "is_default": self.is_default,
            "allow_manual_override": self.allow_manual_override,
            "tags": list(self.tags),
            "notes": self.notes,
            "schedule": dict(self.schedule),
            "worker_revision": self.worker_revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> CollectionSubentryData:
        """Deserialize a persisted collection subentry."""
        raw_tags: object = data.get("tags", ())
        raw_schedule: object = data.get("schedule", {})
        if not isinstance(raw_tags, (list, tuple)) or not all(
            isinstance(tag, str) for tag in cast(tuple[object, ...] | list[object], raw_tags)
        ):
            raise ValueError("collection tags must be strings")
        if not isinstance(raw_schedule, Mapping):
            raise ValueError("collection schedule must be an object")
        tags = tuple(cast(str, tag) for tag in cast(tuple[object, ...] | list[object], raw_tags))
        schedule = dict(cast(Mapping[str, object], raw_schedule))
        starts_at = data.get("starts_at")
        ends_at = data.get("ends_at")
        notes = data.get("notes")
        revision = data.get("worker_revision")
        return cls(
            collection_id=str(data["collection_id"]),
            name=str(data["name"]),
            source_directory=str(data["source_directory"]),
            processing_profile_id=str(data["processing_profile_id"]),
            enabled=bool(data.get("enabled", True)),
            priority=int(str(data.get("priority", 0))),
            starts_at=starts_at if isinstance(starts_at, str) else None,
            ends_at=ends_at if isinstance(ends_at, str) else None,
            is_default=bool(data.get("is_default", False)),
            allow_manual_override=bool(data.get("allow_manual_override", True)),
            tags=tags,
            notes=notes if isinstance(notes, str) else None,
            schedule=schedule,
            worker_revision=revision if isinstance(revision, int) else None,
        )

    def to_policy(self) -> CollectionPolicy:
        """Build the resolver's local collection policy."""
        return CollectionPolicy(
            id=self.collection_id,
            enabled=self.enabled,
            priority=self.priority,
            starts_at=_optional_datetime(self.starts_at),
            ends_at=_optional_datetime(self.ends_at),
            is_default=self.is_default,
            allow_manual_override=self.allow_manual_override,
        )

    def schedules(self) -> tuple[CompilationSchedule, ...]:
        """Build an optional local schedule from this collection policy."""
        return schedules_from_mapping(self.as_dict())

    def worker_create_payload(self) -> dict[str, object]:
        """Return only fields accepted by the Worker collection-create contract."""
        return {
            "id": self.collection_id,
            "name": self.name,
            "source_directory": self.source_directory,
            "processing_profile_id": self.processing_profile_id,
            "enabled": self.enabled,
            "is_default": self.is_default,
        }

    def worker_patch_payload(self) -> dict[str, object]:
        """Return mutable Worker collection fields, excluding local-only policy."""
        return {
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "source_directory": self.source_directory,
            "processing_profile_id": self.processing_profile_id,
            "is_default": self.is_default,
            "allow_manual_override": self.allow_manual_override,
            "tags": list(self.tags),
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class ProfileSubentryData:
    """Durable integration configuration for a Worker processing profile."""

    profile_id: str
    name: str
    settings: Mapping[str, object]
    worker_revision: int | None = None

    def __post_init__(self) -> None:
        _check_identifier(self.profile_id, "profile ID")
        if not self.name.strip():
            raise ValueError("profile name and settings are required")
        if self.worker_revision is not None and self.worker_revision < 1:
            raise ValueError("Worker revision must be positive")

    def with_updates(self, **changes: object) -> ProfileSubentryData:
        if "profile_id" in changes and changes["profile_id"] != self.profile_id:
            raise ValueError("profile IDs are immutable")
        return replace(self, **changes)

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "settings": dict(self.settings),
            "worker_revision": self.worker_revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ProfileSubentryData:
        raw_settings: object = data.get("settings")
        if not isinstance(raw_settings, Mapping):
            raise ValueError("profile settings must be an object")
        settings = dict(cast(Mapping[str, object], raw_settings))
        revision = data.get("worker_revision")
        return cls(
            profile_id=str(data["profile_id"]),
            name=str(data["name"]),
            settings=settings,
            worker_revision=revision if isinstance(revision, int) else None,
        )


def _revision(response: Mapping[str, object]) -> int:
    revision = response.get("revision")
    if isinstance(revision, int) and revision >= 1:
        return revision
    raise WorkerValidationError("Worker response did not include a valid revision")


async def async_sync_collection(
    client: WorkerApiClient, collection: CollectionSubentryData
) -> CollectionSubentryData:
    """Create or revision-patch a Worker collection before saving local policy."""
    create_key = f"collection:{collection.collection_id}:create"
    try:
        if collection.worker_revision is None:
            response = await client.async_create_collection(
                collection.worker_create_payload(), idempotency_key=create_key
            )
            revision = _revision(response)
            # The Worker create contract intentionally has a small safe surface.
            # Persist any remaining Worker-owned settings through its revision API.
            if (
                collection.priority != 0
                or not collection.allow_manual_override
                or collection.tags
                or collection.notes is not None
            ):
                response = await client.async_patch_collection(
                    collection.collection_id,
                    revision,
                    collection.worker_patch_payload(),
                    idempotency_key=f"collection:{collection.collection_id}:patch:{revision}",
                )
        else:
            response = await client.async_patch_collection(
                collection.collection_id,
                collection.worker_revision,
                collection.worker_patch_payload(),
                idempotency_key=(
                    f"collection:{collection.collection_id}:patch:{collection.worker_revision}"
                ),
            )
    except (WorkerApiError, WorkerValidationError) as error:
        raise WorkerValidationError(str(error)) from error
    return collection.with_updates(worker_revision=_revision(response))


async def async_sync_profile(
    client: WorkerApiClient, profile: ProfileSubentryData
) -> ProfileSubentryData:
    """Create or revision-patch a Worker profile before saving local policy."""
    create_key = f"profile:{profile.profile_id}:create"
    try:
        if profile.worker_revision is None:
            response = await client.async_create_profile(
                {
                    "id": profile.profile_id,
                    "name": profile.name,
                    "settings": dict(profile.settings),
                },
                idempotency_key=create_key,
            )
        else:
            response = await client.async_patch_profile(
                profile.profile_id,
                profile.worker_revision,
                {"name": profile.name, "settings": dict(profile.settings)},
                idempotency_key=f"profile:{profile.profile_id}:patch:{profile.worker_revision}",
            )
    except (WorkerApiError, WorkerValidationError) as error:
        raise WorkerValidationError(str(error)) from error
    return profile.with_updates(worker_revision=_revision(response))


def collection_subentries(entry: ConfigEntry) -> tuple[CollectionSubentryData, ...]:
    """Read all durable collection policies from Home Assistant subentries."""
    return tuple(
        CollectionSubentryData.from_dict(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_COLLECTION
    )


def profile_subentries(entry: ConfigEntry) -> tuple[ProfileSubentryData, ...]:
    """Read all durable profile configuration from Home Assistant subentries."""
    return tuple(
        ProfileSubentryData.from_dict(subentry.data)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_PROFILE
    )


def worker_client_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> WorkerApiClient:
    """Reuse runtime client when loaded, otherwise make a short-lived flow client."""
    runtime = hass.data.get("cinema_collections", {}).get(entry.entry_id)
    if runtime is not None:
        return runtime.client
    return WorkerApiClient(
        str(entry.data[CONF_ENDPOINT]), str(entry.data[CONF_TOKEN]), async_get_clientsession(hass)
    )


def async_add_collection_subentry(
    hass: HomeAssistant, entry: ConfigEntry, collection: CollectionSubentryData
) -> None:
    """Persist a worker-synchronized collection policy under a stable unique ID."""
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(collection.as_dict()),
            subentry_type=SUBENTRY_COLLECTION,
            title=collection.name,
            unique_id=collection.collection_id,
        ),
    )


def async_update_collection_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: ConfigSubentry,
    collection: CollectionSubentryData,
) -> None:
    """Persist a revision-checked collection policy update."""
    hass.config_entries.async_update_subentry(
        entry, subentry, data=collection.as_dict(), title=collection.name
    )


_INT_GT_0 = vol.All(vol.Coerce(int), vol.Range(min=1))
_INT_1_16384 = vol.All(vol.Coerce(int), vol.Range(min=1, max=16384))
_INT_1_240 = vol.All(vol.Coerce(int), vol.Range(min=1, max=240))
_FLOAT_0_51 = vol.All(vol.Coerce(float), vol.Range(min=0, max=51))
_FLOAT_M70_M5 = vol.All(vol.Coerce(float), vol.Range(min=-70, max=-5))
_FLOAT_M20_0 = vol.All(vol.Coerce(float), vol.Range(min=-20, max=0))
_FLOAT_1_50 = vol.All(vol.Coerce(float), vol.Range(min=1, max=50))
_FLOAT_0_60 = vol.All(vol.Coerce(float), vol.Range(min=0, max=60))
_FLOAT_GT_0_LE_60 = vol.All(vol.Coerce(float), vol.Range(min=0, max=60, min_included=False))
# HA's schema-to-frontend-form serializer (voluptuous_serialize) only
# recognizes vol.Any(None, X) as its "optional/nullable" idiom; a
# vol.Any(<validator>, "") ("value or blank") pattern falls through every
# branch and raises "Unable to convert schema", which the config-entries
# HTTP endpoint surfaces as a bare 500 rather than a flow error. These two
# fields therefore stay plain `str`; blank-vs-numeric parsing (and the
# positivity check) happens in _profile_settings_from_flow_input instead,
# whose broad `except (KeyError, TypeError, ValueError)` already turns a
# bad value into a normal "invalid_profile" form error.


def _choice_selector(choices: Sequence[tuple[str, str]], *, custom_value: bool = False) -> Any:
    """Build a dropdown of stable values, optionally accepting a typed value.

    ``custom_value`` lets the user type a value outside the offered list, which
    keeps unusual-but-valid values working for fields whose Worker model is an
    open string (codecs, presets, channel counts, sample rates, ...).
    """
    return selector.SelectSelector(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=value, label=label) for value, label in choices
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=custom_value,
        )
    )


_VIDEO_CODEC_OPTIONS = (
    ("libx264", "libx264"),
    ("libx265", "libx265"),
)
_VIDEO_PRESET_OPTIONS = tuple(
    (preset, preset)
    for preset in (
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
)
_VIDEO_H264_PROFILE_OPTIONS = tuple(
    (profile, profile) for profile in ("baseline", "main", "high", "high10", "high422", "high444")
)
_VIDEO_LEVEL_OPTIONS = tuple(
    (level, level)
    for level in ("3.0", "3.1", "4.0", "4.1", "4.2", "5.0", "5.1", "5.2", "6.0", "6.1", "6.2")
)
_VIDEO_PIXEL_FORMAT_OPTIONS = tuple(
    (pixel_format, pixel_format)
    for pixel_format in ("yuv420p", "yuv422p", "yuv444p", "yuv420p10le")
)
_AUDIO_CODEC_OPTIONS = (
    ("aac", "aac"),
    ("libopus", "libopus"),
    ("libmp3lame", "libmp3lame"),
    ("flac", "flac"),
)
_AUDIO_CHANNEL_OPTIONS = (
    ("1", "Mono"),
    ("2", "Stereo"),
    ("6", "5.1"),
    ("8", "7.1"),
)
_AUDIO_SAMPLE_RATE_OPTIONS = (
    ("44100", "44100"),
    ("48000", "48000"),
    ("96000", "96000"),
)


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _to_int(value: object) -> int:
    return int(str(value))


def _to_float(value: object) -> float:
    return float(str(value))


def _transition_durations(transitions: object) -> tuple[float, float]:
    """Read the two supported fade durations from a stored transitions list."""
    if not isinstance(transitions, (list, tuple)):
        return 1.0, 1.0
    items = cast(list[object] | tuple[object, ...], transitions)
    if len(items) < 2:
        return 1.0, 1.0
    durations: list[float] = []
    for item in items[:2]:
        if not isinstance(item, Mapping):
            return 1.0, 1.0
        duration = cast(Mapping[str, object], item).get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            return 1.0, 1.0
        durations.append(float(duration))
    return durations[0], durations[1]


def _profile_form_values(settings: Mapping[str, object]) -> dict[str, object]:
    """Flatten a stored nested profile into field-by-field form defaults."""
    video = _as_mapping(settings.get("video"))
    audio = _as_mapping(settings.get("audio"))
    loudness = _as_mapping(settings.get("loudness"))
    output = _as_mapping(settings.get("output"))
    quality = _as_mapping(video.get("quality"))
    scaling = _as_mapping(video.get("scaling"))
    missing_policy = _as_mapping(audio.get("missing_policy"))
    intro_fade, outro_fade = _transition_durations(settings.get("transitions"))
    return {
        "video_width": video.get("width", 3840),
        "video_height": video.get("height", 2160),
        "video_fps": video.get("fps", 24),
        "video_codec": video.get("codec", "libx264"),
        "video_preset": video.get("preset", "fast"),
        "video_quality_mode": quality.get("mode", "crf"),
        "video_crf": quality.get("crf", 23.0),
        "video_bitrate_kbps": str(quality.get("bitrate_kbps", "") or ""),
        "video_h264_profile": video.get("h264_profile", "high"),
        "video_level": video.get("level", "5.1"),
        "video_pixel_format": video.get("pixel_format", "yuv420p"),
        "video_scaling_strategy": scaling.get("strategy", "aspect_fit"),
        "video_sar_num": scaling.get("sar_num", 1),
        "video_sar_den": scaling.get("sar_den", 1),
        "video_fast_start": video.get("fast_start", True),
        "audio_codec": audio.get("codec", "aac"),
        "audio_bitrate_kbps": audio.get("bitrate_kbps", 192),
        "audio_channels": audio.get("channels", 2),
        "audio_sample_rate": audio.get("sample_rate", 48000),
        "audio_missing_policy": missing_policy.get("mode", "required"),
        "audio_fallback": audio.get("fallback", "none"),
        "audio_pad_or_trim": audio.get("pad_or_trim", True),
        "loudness_mode": loudness.get("mode", "two_pass"),
        "loudness_integrated_lufs": loudness.get("integrated_lufs", -18.0),
        "loudness_true_peak_dbtp": loudness.get("true_peak_dbtp", -1.5),
        "loudness_lra_lu": loudness.get("lra_lu", 11.0),
        "loudness_final_mix_normalization": loudness.get("final_mix_normalization", True),
        "intro_to_clip_fade_seconds": intro_fade,
        "clip_to_outro_fade_seconds": outro_fade,
        "fade_in_seconds": settings.get("fade_in_seconds", 1.0),
        "fade_out_seconds": settings.get("fade_out_seconds", 1.5),
        "output_container": output.get("container", "mp4"),
        "hardware_acceleration": settings.get("hardware_acceleration", False),
        "decode_error_policy": settings.get("decode_error_policy", "warn"),
        "intro_reference": settings.get("intro_reference") or "",
        "outro_reference": settings.get("outro_reference") or "",
        "timeout_seconds": settings.get("timeout_seconds", 300),
        "minimum_segment_duration_seconds": str(
            settings.get("minimum_segment_duration_seconds") or ""
        ),
    }


class CollectionSubentryFlow(config_entries.ConfigSubentryFlow):
    """Create or update a collection policy and synchronize its Worker revision."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Create a collection subentry from native UI fields."""
        return await self._async_step_configure(user_input, existing=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Revision-patch the selected collection subentry."""
        existing = CollectionSubentryData.from_dict(self._get_reconfigure_subentry().data)
        return await self._async_step_configure(user_input, existing=existing)

    async def _async_step_configure(
        self, user_input: dict[str, Any] | None, existing: CollectionSubentryData | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                candidate = _collection_from_flow_input(user_input, existing)
                synchronized = await async_sync_collection(
                    worker_client_for_entry(self.hass, self._get_entry()), candidate
                )
            except WorkerValidationError:
                errors["base"] = "worker_validation"
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_collection"
            else:
                if existing is None:
                    return self.async_create_entry(
                        title=synchronized.name,
                        data=synchronized.as_dict(),
                        unique_id=synchronized.collection_id,
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=synchronized.name,
                    data=synchronized.as_dict(),
                )
        try:
            profiles = await worker_client_for_entry(
                self.hass, self._get_entry()
            ).async_list_profiles()
        except WorkerApiError:
            # A transient Worker outage must not block showing the form; the
            # profile field falls back to free text in that case.
            profiles = ()
        return self.async_show_form(
            step_id="reconfigure" if existing is not None else "user",
            data_schema=_collection_schema(existing, profiles),
            errors=errors,
        )


class ProfileSubentryFlow(config_entries.ConfigSubentryFlow):
    """Create or update a Worker processing-profile subentry.

    The 40 profile fields are presented as a four-step wizard so related
    settings stay together. Input accumulates in ``self._data`` across the
    steps and is only reassembled and synchronized with the Worker on the
    final "output" step.
    """

    def __init__(self) -> None:
        super().__init__()
        self._existing: ProfileSubentryData | None = None
        self._data: dict[str, object] = {}
        self._assets: tuple[str, ...] = ()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Create a profile subentry from native UI fields."""
        self._existing = None
        return await self._async_step_video(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Revision-patch the selected profile subentry."""
        self._existing = ProfileSubentryData.from_dict(self._get_reconfigure_subentry().data)
        return await self._async_step_video(user_input)

    async def async_step_video(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_step_video(user_input)

    async def _async_step_video(self, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            if str(user_input["video_quality_mode"]) == "bitrate" and user_input.get(
                "video_bitrate_kbps"
            ) in (None, ""):
                errors["base"] = "invalid_profile"
            else:
                return await self.async_step_audio()
        return self.async_show_form(
            step_id="video",
            data_schema=_profile_video_schema(self._existing),
            errors=errors,
        )

    async def async_step_audio(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            audio_policy = str(user_input["audio_missing_policy"])
            audio_fallback = str(user_input["audio_fallback"])
            if audio_policy == "required" and audio_fallback != "none":
                errors["base"] = "profile_audio_policy"
            else:
                return await self.async_step_timing()
        return self.async_show_form(
            step_id="audio",
            data_schema=_profile_audio_schema(self._existing),
            errors=errors,
        )

    async def async_step_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_output()
        if not self._assets:
            try:
                self._assets = await worker_client_for_entry(
                    self.hass, self._get_entry()
                ).async_list_assets()
            except WorkerApiError:
                # A transient Worker outage must not block showing the form;
                # the intro/outro fields fall back to free text in that case.
                self._assets = ()
        return self.async_show_form(
            step_id="timing",
            data_schema=_profile_timing_schema(self._existing, self._assets),
            errors=errors,
        )

    async def async_step_output(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            try:
                candidate = _profile_from_flow_input(self._data, self._existing)
                synchronized = await async_sync_profile(
                    worker_client_for_entry(self.hass, self._get_entry()), candidate
                )
            except ProfileAudioPolicyError:
                errors["base"] = "profile_audio_policy"
            except WorkerValidationError:
                errors["base"] = "worker_validation"
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_profile"
            else:
                if self._existing is None:
                    return self.async_create_entry(
                        title=synchronized.name,
                        data=synchronized.as_dict(),
                        unique_id=synchronized.profile_id,
                    )
                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=synchronized.name,
                    data=synchronized.as_dict(),
                )
        return self.async_show_form(
            step_id="output",
            data_schema=_profile_output_schema(self._existing),
            errors=errors,
        )


def _profile_selector(profiles: Sequence[WorkerProfileSummary]) -> Any:
    """Build a dropdown of known Worker profiles, falling back to free text.

    Falls back to a plain text field (instead of an empty, unusable dropdown)
    when the Worker's profile list could not be fetched (e.g. transiently
    unreachable), so the flow never blocks collection creation on that.
    """
    if not profiles:
        return str
    return selector.SelectSelector(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=profile.id, label=f"{profile.name} ({profile.id})")
                for profile in profiles
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=True,
        )
    )


def _asset_reference_selector(assets: Sequence[str]) -> Any:
    """Build a dropdown of the Worker's uploaded assets, falling back to free text.

    Falls back to a plain text field (instead of an empty, unusable dropdown)
    when the Worker's asset list could not be fetched (e.g. transiently
    unreachable), so the flow never blocks profile editing on that. The empty
    value is an explicit "None" choice that serializes to a null reference.
    """
    if not assets:
        return str
    return selector.SelectSelector(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value="", label="None"),
                *(selector.SelectOptionDict(value=name, label=name) for name in assets),
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
            custom_value=True,
        )
    )


def _collection_schema(
    existing: CollectionSubentryData | None,
    profiles: Sequence[WorkerProfileSummary] = (),
) -> vol.Schema:
    values = existing.as_dict() if existing else {}
    raw_schedule: object = values.get("schedule", {})
    schedule: dict[str, object] = (
        dict(cast(Mapping[str, object], raw_schedule)) if isinstance(raw_schedule, Mapping) else {}
    )
    raw_tags: object = values.get("tags", ())
    tags: tuple[object, ...] = (
        tuple(cast(list[object] | tuple[object, ...], raw_tags))
        if isinstance(raw_tags, (list, tuple))
        else ()
    )
    return vol.Schema(
        {
            vol.Required("collection_id", default=values.get("collection_id", "")): str,
            vol.Required("name", default=values.get("name", "")): str,
            vol.Required("source_directory", default=values.get("source_directory", "")): str,
            vol.Required(
                "processing_profile_id", default=values.get("processing_profile_id", "")
            ): _profile_selector(profiles),
            vol.Required("enabled", default=values.get("enabled", True)): bool,
            vol.Required("priority", default=values.get("priority", 0)): vol.Coerce(int),
            vol.Optional("starts_at", default=values.get("starts_at") or ""): str,
            vol.Optional("ends_at", default=values.get("ends_at") or ""): str,
            vol.Required("is_default", default=values.get("is_default", False)): bool,
            vol.Required(
                "allow_manual_override", default=values.get("allow_manual_override", True)
            ): bool,
            vol.Optional("tags", default=", ".join(str(tag) for tag in tags)): str,
            vol.Optional("notes", default=values.get("notes") or ""): str,
            vol.Required("schedule_enabled", default=schedule.get("enabled", False)): bool,
            vol.Optional(
                "schedule_weekdays",
                default=", ".join(str(day) for day in _weekday_values(schedule.get("weekdays"))),
            ): str,
            vol.Optional("schedule_time", default=schedule.get("local_time", "00:00")): str,
            vol.Required(
                "schedule_strategy",
                default=schedule.get("strategy", "scan_and_compile_changed_or_missing"),
            ): vol.In(["scan_and_compile_changed_or_missing", "compile_stale_only", "scan_only"]),
            vol.Required(
                "schedule_skip_if_processing", default=schedule.get("skip_if_processing", True)
            ): bool,
        }
    )


def _weekday_values(value: object) -> tuple[int, ...]:
    """Return a safe weekday tuple for a config-flow field default."""
    if not isinstance(value, (list, tuple)):
        return ()
    values = cast(list[object] | tuple[object, ...], value)
    if not all(isinstance(day, int) for day in values):
        return ()
    return tuple(cast(int, day) for day in values)


def _collection_from_flow_input(
    user_input: Mapping[str, object], existing: CollectionSubentryData | None
) -> CollectionSubentryData:
    collection_id = str(user_input["collection_id"])
    if existing is not None and collection_id != existing.collection_id:
        raise ValueError("collection IDs are immutable")
    weekdays = tuple(
        int(value.strip())
        for value in str(user_input.get("schedule_weekdays", "")).split(",")
        if value.strip()
    )
    schedule = {
        "enabled": bool(user_input["schedule_enabled"]),
        "weekdays": list(weekdays),
        "local_time": str(user_input.get("schedule_time", "00:00")),
        "strategy": str(user_input["schedule_strategy"]),
        "skip_if_processing": bool(user_input["schedule_skip_if_processing"]),
    }
    # Validate schedule shape now, returning errors in the same flow that supplied it.
    CompilationSchedule(
        collection_id=collection_id,
        enabled=bool(schedule["enabled"]),
        weekdays=weekdays,
        local_time=datetime.strptime(str(schedule["local_time"]), "%H:%M").time(),
        strategy=str(schedule["strategy"]),
        skip_if_processing=bool(schedule["skip_if_processing"]),
    )
    return CollectionSubentryData(
        collection_id=collection_id,
        name=str(user_input["name"]),
        source_directory=str(user_input["source_directory"]),
        processing_profile_id=str(user_input["processing_profile_id"]),
        enabled=bool(user_input["enabled"]),
        priority=int(str(user_input["priority"])),
        starts_at=str(user_input.get("starts_at") or "") or None,
        ends_at=str(user_input.get("ends_at") or "") or None,
        is_default=bool(user_input["is_default"]),
        allow_manual_override=bool(user_input["allow_manual_override"]),
        tags=tuple(
            tag.strip() for tag in str(user_input.get("tags", "")).split(",") if tag.strip()
        ),
        notes=str(user_input.get("notes") or "") or None,
        schedule=schedule,
        worker_revision=existing.worker_revision if existing else None,
    )


def _profile_form_defaults(existing: ProfileSubentryData | None) -> dict[str, object]:
    """Return field-by-field form defaults for a profile wizard step."""
    values = existing.as_dict() if existing else {}
    raw_settings: object = values.get("settings", {})
    return _profile_form_values(
        dict(cast(Mapping[str, object], raw_settings)) if isinstance(raw_settings, Mapping) else {}
    )


def _profile_video_schema(existing: ProfileSubentryData | None) -> vol.Schema:
    """Identity plus every video encoding, quality and scaling field."""
    values = existing.as_dict() if existing else {}
    form = _profile_form_defaults(existing)
    return vol.Schema(
        {
            vol.Required("profile_id", default=values.get("profile_id", "")): str,
            vol.Required("name", default=values.get("name", "")): str,
            vol.Required("video_width", default=form["video_width"]): _INT_1_16384,
            vol.Required("video_height", default=form["video_height"]): _INT_1_16384,
            vol.Required("video_fps", default=form["video_fps"]): _INT_1_240,
            vol.Required("video_codec", default=form["video_codec"]): _choice_selector(
                _VIDEO_CODEC_OPTIONS, custom_value=True
            ),
            vol.Required("video_preset", default=form["video_preset"]): _choice_selector(
                _VIDEO_PRESET_OPTIONS, custom_value=True
            ),
            vol.Required(
                "video_quality_mode", default=form["video_quality_mode"]
            ): _choice_selector([("crf", "CRF"), ("bitrate", "Bitrate")]),
            vol.Required("video_crf", default=form["video_crf"]): _FLOAT_0_51,
            vol.Optional("video_bitrate_kbps", default=form["video_bitrate_kbps"]): str,
            vol.Required(
                "video_h264_profile", default=form["video_h264_profile"]
            ): _choice_selector(_VIDEO_H264_PROFILE_OPTIONS, custom_value=True),
            vol.Required("video_level", default=form["video_level"]): _choice_selector(
                _VIDEO_LEVEL_OPTIONS, custom_value=True
            ),
            vol.Required(
                "video_pixel_format", default=form["video_pixel_format"]
            ): _choice_selector(_VIDEO_PIXEL_FORMAT_OPTIONS, custom_value=True),
            vol.Required(
                "video_scaling_strategy", default=form["video_scaling_strategy"]
            ): _choice_selector([("aspect_fit", "Aspect fit"), ("crop", "Crop")]),
            vol.Required("video_sar_num", default=form["video_sar_num"]): _INT_GT_0,
            vol.Required("video_sar_den", default=form["video_sar_den"]): _INT_GT_0,
            vol.Required("video_fast_start", default=form["video_fast_start"]): bool,
        }
    )


def _profile_audio_schema(existing: ProfileSubentryData | None) -> vol.Schema:
    """Every audio encoding and loudness field."""
    form = _profile_form_defaults(existing)
    return vol.Schema(
        {
            vol.Required("audio_codec", default=form["audio_codec"]): _choice_selector(
                _AUDIO_CODEC_OPTIONS, custom_value=True
            ),
            vol.Required("audio_bitrate_kbps", default=form["audio_bitrate_kbps"]): _INT_GT_0,
            vol.Required("audio_channels", default=str(form["audio_channels"])): _choice_selector(
                _AUDIO_CHANNEL_OPTIONS, custom_value=True
            ),
            vol.Required(
                "audio_sample_rate", default=str(form["audio_sample_rate"])
            ): _choice_selector(_AUDIO_SAMPLE_RATE_OPTIONS, custom_value=True),
            vol.Required(
                "audio_missing_policy", default=form["audio_missing_policy"]
            ): _choice_selector([("required", "Required"), ("silence", "Silence")]),
            vol.Required("audio_fallback", default=form["audio_fallback"]): _choice_selector(
                [("none", "None"), ("silence", "Silence")]
            ),
            vol.Required("audio_pad_or_trim", default=form["audio_pad_or_trim"]): bool,
            vol.Required("loudness_mode", default=form["loudness_mode"]): _choice_selector(
                [("two_pass", "Two pass"), ("disabled", "Disabled")]
            ),
            vol.Required(
                "loudness_integrated_lufs", default=form["loudness_integrated_lufs"]
            ): _FLOAT_M70_M5,
            vol.Required(
                "loudness_true_peak_dbtp", default=form["loudness_true_peak_dbtp"]
            ): _FLOAT_M20_0,
            vol.Required("loudness_lra_lu", default=form["loudness_lra_lu"]): _FLOAT_1_50,
            vol.Required(
                "loudness_final_mix_normalization", default=form["loudness_final_mix_normalization"]
            ): bool,
        }
    )


def _profile_timing_schema(
    existing: ProfileSubentryData | None, assets: Sequence[str] = ()
) -> vol.Schema:
    """Intro/outro references and fade and segment timing fields."""
    form = _profile_form_defaults(existing)
    return vol.Schema(
        {
            vol.Optional("intro_reference", default=form["intro_reference"]): (
                _asset_reference_selector(assets)
            ),
            vol.Optional("outro_reference", default=form["outro_reference"]): (
                _asset_reference_selector(assets)
            ),
            vol.Required(
                "intro_to_clip_fade_seconds", default=form["intro_to_clip_fade_seconds"]
            ): _FLOAT_GT_0_LE_60,
            vol.Required(
                "clip_to_outro_fade_seconds", default=form["clip_to_outro_fade_seconds"]
            ): _FLOAT_GT_0_LE_60,
            vol.Required("fade_in_seconds", default=form["fade_in_seconds"]): _FLOAT_0_60,
            vol.Required("fade_out_seconds", default=form["fade_out_seconds"]): _FLOAT_0_60,
            vol.Optional(
                "minimum_segment_duration_seconds",
                default=form["minimum_segment_duration_seconds"],
            ): str,
        }
    )


def _profile_output_schema(existing: ProfileSubentryData | None) -> vol.Schema:
    """Output container, hardware, decode-error and timeout fields."""
    form = _profile_form_defaults(existing)
    return vol.Schema(
        {
            vol.Required("output_container", default=form["output_container"]): _choice_selector(
                [("mp4", "MP4"), ("mkv", "MKV"), ("webm", "WebM")]
            ),
            vol.Required("hardware_acceleration", default=form["hardware_acceleration"]): bool,
            vol.Required(
                "decode_error_policy", default=form["decode_error_policy"]
            ): _choice_selector([("warn", "Warn"), ("fail", "Fail")]),
            vol.Required("timeout_seconds", default=form["timeout_seconds"]): _INT_GT_0,
        }
    )


def _profile_settings_from_flow_input(user_input: Mapping[str, object]) -> dict[str, object]:
    """Reassemble flat flow fields into the Worker's nested profile contract."""
    quality_mode = str(user_input["video_quality_mode"])
    if quality_mode == "bitrate":
        raw_bitrate = user_input.get("video_bitrate_kbps")
        if raw_bitrate in (None, ""):
            raise ValueError("bitrate video quality requires a bitrate")
        quality: dict[str, object] = {"mode": "bitrate", "bitrate_kbps": _to_int(raw_bitrate)}
    else:
        quality = {"mode": "crf", "crf": _to_float(user_input["video_crf"])}

    scaling_strategy = str(user_input["video_scaling_strategy"])
    scaling: dict[str, object] = {
        "strategy": scaling_strategy,
        "width": _to_int(user_input["video_width"]),
        "height": _to_int(user_input["video_height"]),
    }
    if scaling_strategy == "aspect_fit":
        scaling["sar_num"] = _to_int(user_input["video_sar_num"])
        scaling["sar_den"] = _to_int(user_input["video_sar_den"])

    audio_policy = str(user_input["audio_missing_policy"])
    audio_fallback = str(user_input["audio_fallback"])
    if audio_policy == "required" and audio_fallback != "none":
        raise ProfileAudioPolicyError("required audio cannot use a silence fallback")

    loudness_mode = str(user_input["loudness_mode"])
    loudness: dict[str, object] = {
        "mode": loudness_mode,
        "final_mix_normalization": bool(user_input["loudness_final_mix_normalization"]),
    }
    if loudness_mode == "two_pass":
        loudness["integrated_lufs"] = _to_float(user_input["loudness_integrated_lufs"])
        loudness["true_peak_dbtp"] = _to_float(user_input["loudness_true_peak_dbtp"])
        loudness["lra_lu"] = _to_float(user_input["loudness_lra_lu"])

    output_container = str(user_input["output_container"])
    raw_minimum = user_input.get("minimum_segment_duration_seconds")
    minimum_segment_duration = _to_float(raw_minimum) if raw_minimum not in (None, "") else None

    return {
        "video": {
            "width": _to_int(user_input["video_width"]),
            "height": _to_int(user_input["video_height"]),
            "fps": _to_int(user_input["video_fps"]),
            "codec": str(user_input["video_codec"]),
            "preset": str(user_input["video_preset"]),
            "quality": quality,
            "h264_profile": str(user_input["video_h264_profile"]),
            "level": str(user_input["video_level"]),
            "pixel_format": str(user_input["video_pixel_format"]),
            "scaling": scaling,
            "fast_start": bool(user_input["video_fast_start"]),
        },
        "audio": {
            "codec": str(user_input["audio_codec"]),
            "bitrate_kbps": _to_int(user_input["audio_bitrate_kbps"]),
            "channels": _to_int(user_input["audio_channels"]),
            "sample_rate": _to_int(user_input["audio_sample_rate"]),
            "missing_policy": {"mode": audio_policy},
            "fallback": audio_fallback,
            "pad_or_trim": bool(user_input["audio_pad_or_trim"]),
        },
        "loudness": loudness,
        "transitions": [
            {
                "type": "fade",
                "duration_seconds": _to_float(user_input["intro_to_clip_fade_seconds"]),
                "from_segment": "intro",
                "to_segment": "clip",
            },
            {
                "type": "fade",
                "duration_seconds": _to_float(user_input["clip_to_outro_fade_seconds"]),
                "from_segment": "clip",
                "to_segment": "outro",
            },
        ],
        "fade_in_seconds": _to_float(user_input["fade_in_seconds"]),
        "fade_out_seconds": _to_float(user_input["fade_out_seconds"]),
        "output": {
            "container": output_container,
            "extension": output_container,
            "atomic_finalize": True,
            "temporary_output": True,
        },
        "hardware_acceleration": bool(user_input["hardware_acceleration"]),
        "decode_error_policy": str(user_input["decode_error_policy"]),
        "intro_reference": str(user_input.get("intro_reference") or "") or None,
        "outro_reference": str(user_input.get("outro_reference") or "") or None,
        "timeout_seconds": _to_int(user_input["timeout_seconds"]),
        "minimum_segment_duration_seconds": minimum_segment_duration,
    }


def _profile_from_flow_input(
    user_input: Mapping[str, object], existing: ProfileSubentryData | None
) -> ProfileSubentryData:
    profile_id = str(user_input["profile_id"])
    if existing is not None and profile_id != existing.profile_id:
        raise ValueError("profile IDs are immutable")
    return ProfileSubentryData(
        profile_id=profile_id,
        name=str(user_input["name"]),
        settings=_profile_settings_from_flow_input(user_input),
        worker_revision=existing.worker_revision if existing else None,
    )
