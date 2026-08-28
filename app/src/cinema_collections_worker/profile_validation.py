"""Typed processing profiles and deterministic profile fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paths import validate_filename, validate_relative_path


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CrfQuality(ProfileModel):
    mode: Literal["crf"] = "crf"
    crf: float = Field(ge=0, le=51)
    bitrate_kbps: int | None = None

    @model_validator(mode="after")
    def no_bitrate(self) -> CrfQuality:
        if self.bitrate_kbps is not None:
            raise ValueError("CRF quality cannot be combined with bitrate")
        return self


class BitrateQuality(ProfileModel):
    mode: Literal["bitrate"] = "bitrate"
    bitrate_kbps: int = Field(gt=0)
    crf: float | None = None

    @model_validator(mode="after")
    def no_crf(self) -> BitrateQuality:
        if self.crf is not None:
            raise ValueError("bitrate quality cannot be combined with CRF")
        return self


QualityMode = Annotated[CrfQuality | BitrateQuality, Field(discriminator="mode")]


class AspectFitScaling(ProfileModel):
    strategy: Literal["aspect_fit"] = "aspect_fit"
    width: int = Field(gt=0, le=16384)
    height: int = Field(gt=0, le=16384)
    sar_num: int = Field(default=1, gt=0)
    sar_den: int = Field(default=1, gt=0)


class CropScaling(ProfileModel):
    strategy: Literal["crop"] = "crop"
    width: int = Field(gt=0, le=16384)
    height: int = Field(gt=0, le=16384)


ScalingStrategy = Annotated[AspectFitScaling | CropScaling, Field(discriminator="strategy")]


class RequiredAudio(ProfileModel):
    mode: Literal["required"] = "required"


class SilenceAudio(ProfileModel):
    mode: Literal["silence"] = "silence"


AudioMissingPolicy = Annotated[RequiredAudio | SilenceAudio, Field(discriminator="mode")]


class TwoPassLoudness(ProfileModel):
    mode: Literal["two_pass"] = "two_pass"
    integrated_lufs: float = Field(ge=-70, le=-5)
    true_peak_dbtp: float = Field(ge=-20, le=0)
    lra_lu: float = Field(ge=1, le=50)
    final_mix_normalization: bool = True


class DisabledLoudness(ProfileModel):
    mode: Literal["disabled"] = "disabled"
    final_mix_normalization: bool = False


LoudnessMode = Annotated[TwoPassLoudness | DisabledLoudness, Field(discriminator="mode")]


class FadeTransition(ProfileModel):
    type: Literal["fade"] = "fade"
    duration_seconds: float = Field(gt=0, le=60)
    from_segment: Literal["intro", "clip", "outro"] = "clip"
    to_segment: Literal["intro", "clip", "outro"] = "clip"


TransitionSettings = Annotated[FadeTransition, Field(discriminator="type")]


class VideoSettings(ProfileModel):
    width: int = Field(default=3840, gt=0, le=16384)
    height: int = Field(default=2160, gt=0, le=16384)
    fps: int = Field(default=24, gt=0, le=240)
    codec: str = "libx264"
    preset: str = "fast"
    quality: QualityMode = CrfQuality(crf=23)
    h264_profile: str = "high"
    level: str = "5.1"
    pixel_format: str = "yuv420p"
    scaling: ScalingStrategy = AspectFitScaling(width=3840, height=2160)
    fast_start: bool = True


class AudioSettings(ProfileModel):
    codec: str = "aac"
    bitrate_kbps: int = Field(default=192, gt=0)
    channels: int = Field(default=2, gt=0, le=8)
    sample_rate: int = Field(default=48000, gt=0)
    missing_policy: AudioMissingPolicy = RequiredAudio()
    fallback: Literal["none", "silence"] = "none"
    pad_or_trim: bool = True

    @model_validator(mode="after")
    def validate_policy(self) -> AudioSettings:
        if self.missing_policy.mode == "required" and self.fallback != "none":
            raise ValueError("required audio cannot use a silence fallback")
        return self


class OutputSettings(ProfileModel):
    container: Literal["mp4", "mkv", "webm"] = "mp4"
    extension: str = "mp4"
    atomic_finalize: bool = True
    temporary_output: bool = True

    @model_validator(mode="after")
    def validate_extension(self) -> OutputSettings:
        if not re.fullmatch(r"[A-Za-z0-9]{1,8}", self.extension) or self.extension.lower() not in {
            "mp4",
            "mkv",
            "webm",
        }:
            raise ValueError("output extension is not safe or supported")
        return self

    @field_validator("extension", mode="before")
    @classmethod
    def normalize_extension(cls, value: object) -> object:
        if isinstance(value, str) and value.startswith("."):
            return value[1:]
        return value


class ProcessingProfile(ProfileModel):
    """Versioned, structured compilation settings; no shell/filter strings."""

    profile_version: int = Field(default=1, ge=1)
    video: VideoSettings = VideoSettings()
    audio: AudioSettings = AudioSettings()
    loudness: LoudnessMode = TwoPassLoudness(integrated_lufs=-18, true_peak_dbtp=-1.5, lra_lu=11)
    transitions: list[TransitionSettings] = Field(
        default_factory=lambda: [
            FadeTransition(duration_seconds=1, from_segment="intro", to_segment="clip"),
            FadeTransition(duration_seconds=1, from_segment="clip", to_segment="outro"),
        ]
    )
    fade_in_seconds: float = Field(default=1, ge=0, le=60)
    fade_out_seconds: float = Field(default=1.5, ge=0, le=60)
    output: OutputSettings = OutputSettings()
    hardware_acceleration: bool = False
    decode_error_policy: Literal["warn", "fail"] = "warn"
    intro_reference: str | None = None
    outro_reference: str | None = None
    timeout_seconds: int = Field(default=300, gt=0)
    minimum_segment_duration_seconds: float | None = Field(default=None, gt=0)

    @field_validator("intro_reference", "outro_reference")
    @classmethod
    def validate_asset_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        relative = validate_relative_path(value)
        validate_filename(Path(relative).name)
        return relative

    @model_validator(mode="after")
    def validate_profile_math(self) -> ProcessingProfile:
        if self.fade_in_seconds + self.fade_out_seconds > 120:
            raise ValueError("fade durations exceed the supported timeline")
        if (
            self.minimum_segment_duration_seconds is not None
            and self.fade_in_seconds + self.fade_out_seconds > self.minimum_segment_duration_seconds
        ):
            raise ValueError("fade durations exceed the minimum segment duration")
        if any(t.type != "fade" for t in self.transitions):
            raise ValueError("unsupported transition type")
        return self


class AssetFingerprints(ProfileModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    intro: str | None = Field(default=None, alias="intro_fingerprint")
    outro: str | None = Field(default=None, alias="outro_fingerprint")
    source: str | None = Field(default=None, alias="source_fingerprint")


def validate_profile(profile: ProcessingProfile) -> ProcessingProfile:
    """Re-validate and return a profile at the processing boundary."""
    return ProcessingProfile.model_validate(profile.model_dump(mode="python"))


def profile_fingerprint(
    profile: ProcessingProfile, assets: AssetFingerprints | Mapping[str, object]
) -> str:
    """Return a stable SHA-256 fingerprint of settings and referenced assets."""
    assets_model = AssetFingerprints.model_validate(assets)
    payload = {
        "profile": validate_profile(profile).model_dump(mode="json"),
        "assets": assets_model.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
