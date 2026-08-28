"""Safe FFmpeg argument construction from validated processing profiles."""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .profile_validation import ProcessingProfile, validate_profile

if TYPE_CHECKING:
    from .jobs import JobRecord


class FfmpegCommandBuilder:
    """Build argv vectors; profile data never supplies shell or raw filter text."""

    def __init__(self, executable: str = "ffmpeg") -> None:
        self.executable = executable

    @staticmethod
    def _video_filter(profile: ProcessingProfile) -> str:
        scaling = profile.video.scaling
        if scaling.strategy == "crop":
            return f"scale={scaling.width}:{scaling.height}:force_original_aspect_ratio=increase,crop={scaling.width}:{scaling.height},setsar=1"
        return (
            f"scale={scaling.width}:{scaling.height}:force_original_aspect_ratio=decrease,"
            f"pad={scaling.width}:{scaling.height}:(ow-iw)/2:(oh-ih)/2,setsar={scaling.sar_num}/{scaling.sar_den}"
        )

    @staticmethod
    def _audio_filter(profile: ProcessingProfile, duration_seconds: float) -> str:
        items = [
            f"aformat=channel_layouts={'stereo' if profile.audio.channels == 2 else f'{profile.audio.channels}c'}",
            f"aresample={profile.audio.sample_rate}",
        ]
        if profile.audio.pad_or_trim:
            items.extend(["apad", f"atrim=duration={max(0.0, duration_seconds):.3f}"])
        return ",".join(items)

    @staticmethod
    def _loudnorm(
        profile: ProcessingProfile, measured: Mapping[str, float] | None = None
    ) -> str | None:
        if profile.loudness.mode == "disabled":
            return None
        filter_text = (
            f"loudnorm=I={profile.loudness.integrated_lufs:g}:"
            f"TP={profile.loudness.true_peak_dbtp:g}:LRA={profile.loudness.lra_lu:g}"
        )
        if measured:
            fields = {
                "input_i": "measured_I",
                "input_tp": "measured_TP",
                "input_lra": "measured_LRA",
                "input_thresh": "measured_thresh",
                "target_offset": "offset",
            }
            filter_text += "".join(
                f":{output}={measured[input_name]:g}"
                for input_name, output in fields.items()
                if input_name in measured
            )
        return filter_text

    def build_segment_loudness_analysis(self, job: JobRecord) -> list[str]:
        profile = validate_profile(ProcessingProfile.model_validate(job.profile_settings))
        loudnorm = self._loudnorm(profile)
        if loudnorm is None or job.source_path is None:
            return []
        return [
            self.executable,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(job.source_path),
            "-af",
            loudnorm + ":print_format=json",
            "-f",
            "null",
            "-",
        ]

    def build_final_loudness_analysis(self, job: JobRecord) -> list[str]:
        """Return a separate final-mix analysis argv when two-pass loudness is enabled."""

        return self.build_segment_loudness_analysis(job)

    def build(
        self, job: JobRecord, measured_loudness: Mapping[str, float] | None = None
    ) -> list[str]:
        profile = validate_profile(ProcessingProfile.model_validate(job.profile_settings))
        if job.source_path is None or job.temporary_output_path is None:
            raise ValueError("job paths must be resolved before building FFmpeg arguments")
        command = [
            self.executable,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-progress",
            "pipe:1",
            "-i",
            str(job.source_path),
        ]
        inputs: list[tuple[str, Path]] = [("clip", job.source_path)]
        if job.intro_path is not None:
            command.extend(["-i", str(job.intro_path)])
            inputs.append(("intro", job.intro_path))
        if job.outro_path is not None:
            command.extend(["-i", str(job.outro_path)])
            inputs.append(("outro", job.outro_path))

        graph: list[str] = []
        video_filter = self._video_filter(profile)
        audio_filter = self._audio_filter(profile, job.duration_seconds)
        for index, (name, _path) in enumerate(inputs):
            graph.append(f"[{index}:v]{video_filter}[v_{name}]")
            if name == "clip" and not job.has_audio:
                if profile.audio.missing_policy.mode != "silence":
                    raise ValueError("source audio is required by this processing profile")
                graph.append(
                    f"anullsrc=channel_layout=stereo:sample_rate={profile.audio.sample_rate},"
                    f"{audio_filter}[a_{name}]"
                )
            else:
                graph.append(f"[{index}:a]{audio_filter}[a_{name}]")
        video_label, audio_label = "v_clip", "a_clip"
        transition = profile.transitions[0].duration_seconds if profile.transitions else 0.0
        if job.intro_path is not None:
            intro_offset = max(0.0, job.intro_duration_seconds - transition)
            graph.append(
                f"[v_intro][{video_label}]xfade=transition=fade:duration={transition:g}:offset={intro_offset:g}[v_intro_clip]"
            )
            graph.append(f"[a_intro][{audio_label}]acrossfade=d={transition:g}[a_intro_clip]")
            video_label, audio_label = "v_intro_clip", "a_intro_clip"
        if job.outro_path is not None:
            preceding_duration = job.duration_seconds
            if job.intro_path is not None:
                preceding_duration += job.intro_duration_seconds - transition
            outro_offset = max(0.0, preceding_duration - transition)
            graph.append(
                f"[{video_label}][v_outro]xfade=transition=fade:duration={transition:g}:offset={outro_offset:g}[v_final_transition]"
            )
            graph.append(f"[{audio_label}][a_outro]acrossfade=d={transition:g}[a_final_transition]")
            video_label, audio_label = "v_final_transition", "a_final_transition"
        fade_out_start = max(0.0, job.duration_seconds - profile.fade_out_seconds)
        graph.append(
            f"[{video_label}]fade=t=in:st=0:d={profile.fade_in_seconds:g},"
            f"fade=t=out:st={fade_out_start:g}:d={profile.fade_out_seconds:g}[v_final]"
        )
        loudnorm = self._loudnorm(profile, measured_loudness)
        final_audio = f"[{audio_label}]"
        if loudnorm is not None and profile.loudness.final_mix_normalization:
            final_audio += loudnorm + ","
        graph.append(final_audio + "anull[a_final]")
        command.extend(
            ["-filter_complex", ";".join(graph), "-map", "[v_final]", "-map", "[a_final]"]
        )
        command.extend(
            [
                "-r",
                str(profile.video.fps),
                "-c:v",
                profile.video.codec,
                "-preset",
                profile.video.preset,
            ]
        )
        if profile.video.quality.mode == "crf":
            command.extend(["-crf", f"{profile.video.quality.crf:g}"])
        else:
            command.extend(["-b:v", f"{profile.video.quality.bitrate_kbps}k"])
        command.extend(
            [
                "-pix_fmt",
                profile.video.pixel_format,
                "-c:a",
                profile.audio.codec,
                "-b:a",
                f"{profile.audio.bitrate_kbps}k",
            ]
        )
        if profile.video.fast_start and profile.output.container == "mp4":
            command.extend(["-movflags", "+faststart"])
        command.append(str(job.temporary_output_path))
        return command
