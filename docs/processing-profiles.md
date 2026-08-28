# Processing profiles

Processing profiles are structured Pydantic values. The Worker accepts typed
codec, scaling, audio, loudness, transition, timing, and output settings; it
does not accept shell syntax or user-provided FFmpeg filter strings.

`compatibility_4k_loudness_profile()` is the editable baseline: 3840x2160 at
24 fps, aspect-fit padding, libx264 fast/CRF 23, AAC stereo 192 kbps at 48 kHz,
one-second segment fades, one-second fade-in and 1.5-second fade-out, and
two-pass EBU R128 normalization at -18 LUFS, -1.5 dBTP, and 11 LU.

Hardware acceleration is disabled by default. Outputs are written to tracked
temporary paths and atomically finalized. Intro/outro references are persisted
as safe references and resolved through `SafePathResolver` only when a job is
queued.

Decode errors default to `warn`: the Worker records the decode diagnostic and
continues when the output remains valid. Set `decode_error_policy` to `fail`
when any decode diagnostic must reject the job.
