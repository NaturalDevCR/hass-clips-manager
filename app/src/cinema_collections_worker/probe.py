"""Isolated, bounded ffprobe metadata collection."""
# ruff: noqa: E501
# ffprobe's JSON schema is intentionally validated at runtime.
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class MediaProbeResult:
    valid: bool
    duration_seconds: float = 0.0
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    has_audio: bool = False
    streams: list[dict[str, object]] = field(default_factory=list)
    size_bytes: int | None = None
    error: str | None = None


def _rate(value: object) -> float | None:
    try:
        if isinstance(value, str) and "/" in value:
            return float(Fraction(value))
        if isinstance(value, (int, float, str)):
            return float(value)
        return None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


class ProbeClient:
    def __init__(self, executable: str = "ffprobe", timeout_seconds: float = 30.0) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def probe(self, path: Path) -> MediaProbeResult:
        command = [
            self.executable,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False
        )
        try:
            stdout, _stderr = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return MediaProbeResult(valid=False, error="media probe timed out")
        if process.returncode != 0:
            return MediaProbeResult(valid=False, error="media probe failed")
        try:
            payload = json.loads(stdout.decode("utf-8"))
            streams = payload.get("streams", [])
            fmt = payload.get("format", {})
            if not isinstance(streams, list) or not isinstance(fmt, dict):
                raise ValueError
            video = next(
                (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), None
            )
            duration = float(fmt["duration"])
            if duration < 0 or video is None:
                raise ValueError
            parsed_streams = [s for s in streams if isinstance(s, dict)]
            return MediaProbeResult(
                valid=True,
                duration_seconds=duration,
                width=int(video["width"]) if video.get("width") is not None else None,
                height=int(video["height"]) if video.get("height") is not None else None,
                frame_rate=_rate(video.get("r_frame_rate")),
                has_audio=any(s.get("codec_type") == "audio" for s in parsed_streams),
                streams=parsed_streams,
                size_bytes=int(fmt["size"]) if fmt.get("size") is not None else None,
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            return MediaProbeResult(valid=False, error="media probe returned malformed metadata")
