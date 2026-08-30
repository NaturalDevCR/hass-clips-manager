# Processing profiles

Processing profiles are structured Pydantic values. The Worker accepts typed
codec, scaling, audio, loudness, transition, timing, and output settings; it
does not accept shell syntax or user-provided FFmpeg filter strings.

## The built-in baseline

`compatibility-4k-loudness` (**Compatibility 4K Loudness Profile**) is the
editable baseline: 3840x2160 at 24 fps, aspect-fit padding, libx264 fast/CRF 23,
AAC stereo 192 kbps at 48 kHz, one-second segment fades, one-second fade-in and
1.5-second fade-out, and two-pass EBU R128 normalization at -18 LUFS, -1.5 dBTP,
and 11 LU. It is seeded into the Worker database automatically and can be
reconfigured in place.

## Editing profiles

Profiles are edited through the integration, not the Worker API. Open the
integration entry in **Settings → Devices & services** and add or reconfigure a
**Processing profile** config subentry. The editor is a field-by-field form;
every field below maps to one form control. Saving synchronizes the profile to
the Worker and reports the Worker's validation errors back in the form.

You only need a second profile when a collection must compile differently from
the baseline.

## Field reference

| Group | Field | Allowed values / type | Default |
| --- | --- | --- | --- |
| Identity | Profile ID | lowercase URL-safe slug, immutable after creation | — |
| Identity | Name | text | — |
| Video | Video width | integer 1–16384 | 3840 |
| Video | Video height | integer 1–16384 | 2160 |
| Video | Video frame rate | integer 1–240 | 24 |
| Video | Video codec | text | libx264 |
| Video | Video preset | text | fast |
| Video | Video quality mode | `crf`, `bitrate` | crf |
| Video | Video CRF | float 0–51 | 23 |
| Video | Video bitrate (kbps) | integer, bitrate mode only | blank |
| Video | Video H.264 profile | text | high |
| Video | Video level | text | 5.1 |
| Video | Video pixel format | text | yuv420p |
| Video | Video scaling strategy | `aspect_fit`, `crop` | aspect_fit |
| Video | Video pixel aspect ratio (numerator) | integer > 0 | 1 |
| Video | Video pixel aspect ratio (denominator) | integer > 0 | 1 |
| Video | Video fast start | boolean | true |
| Audio | Audio codec | text | aac |
| Audio | Audio bitrate (kbps) | integer > 0 | 192 |
| Audio | Audio channels | integer 1–8 | 2 |
| Audio | Audio sample rate | integer > 0 | 48000 |
| Audio | Audio missing policy | `required`, `silence` | required |
| Audio | Audio fallback | `none`, `silence` | none |
| Audio | Pad or trim audio to each segment | boolean | true |
| Loudness | Loudness mode | `two_pass`, `disabled` | two_pass |
| Loudness | Loudness integrated (LUFS) | float -70…-5 | -18 |
| Loudness | Loudness true peak (dBTP) | float -20…0 | -1.5 |
| Loudness | Loudness range (LU) | float 1–50 | 11 |
| Loudness | Final mix loudness normalization | boolean | true |
| Transitions | Intro-to-clip fade (seconds) | float > 0 and ≤ 60 | 1 |
| Transitions | Clip-to-outro fade (seconds) | float > 0 and ≤ 60 | 1 |
| Transitions | Fade in (seconds) | float 0–60 | 1 |
| Transitions | Fade out (seconds) | float 0–60 | 1.5 |
| Output | Output container | `mp4`, `mkv`, `webm` | mp4 |
| Acceleration | Hardware acceleration | boolean | false |
| Decode policy | Decode error policy | `warn`, `fail` | warn |
| Assets | Intro asset filename | bare asset filename or None | none |
| Assets | Outro asset filename | bare asset filename or None | none |
| Timeout | Per-pass timeout (seconds) | integer > 0 | 300 |
| Segment | Minimum segment duration (seconds) | float > 0 or blank | blank |

Additional validation rules apply across fields:

- `video_quality_mode: bitrate` requires a value in **Video bitrate (kbps)**.
- `audio_missing_policy: required` forbids an `audio_fallback` other than
  `none`.
- `fade_in_seconds + fade_out_seconds` must not exceed 120 seconds, and must
  not exceed the minimum segment duration when one is set.
- Outputs are written to tracked temporary paths and atomically finalized.

## Intro and outro assets

**Intro asset filename** and **Outro asset filename** expect a bare filename
that must already exist in the Worker's assets storage. In the editor they are
dropdowns populated from the uploaded assets, with an explicit **None** choice;
free-text entry is still allowed, and a plain text field is shown when the
Worker is unreachable. Upload assets in the Library Manager's **Intro/outro
assets** section and reference the exact filename it lists (see the asset step
in [getting-started.md](getting-started.md)).

Intro and outro are chosen independently: they may be different files, or the
same file in both fields. When both reference the same asset, the Worker reuses
the intro's loudness analysis for the outro instead of analyzing it twice.
Referenced assets are resolved through `SafePathResolver` only when a job is
queued.

Decode errors default to `warn`: the Worker records the decode diagnostic and
continues when the output remains valid. Set `decode_error_policy` to `fail`
when any decode diagnostic must reject the job.