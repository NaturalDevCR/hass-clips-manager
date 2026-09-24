# Cast-safe compiled clip margins

## Problem

Google Cast can show its startup controls before a selected clip's picture is
ready and its own interface again when playback ends. Home Assistant needs
reliable content boundaries inside the compiled file so it can coordinate a
projector around those Cast screens. The integration must continue preparing
and describing media only; Home Assistant owns device coordination.

## Goals

- Add configurable black video and silent audio before and after every newly
  compiled clip, defaulting to two seconds at each end.
- Report final-file duration and content boundaries in seconds, including
  fractional values, in the Home Assistant `select_next_clip` result.
- Preserve all source content and its A/V synchronization. Do not apply fades
  across the content to create these margins.
- Include the configured margins in compilation identity so changing them
  recompiles affected clips.
- Keep the existing `duration_seconds` response field and treat old compiled
  files without margin metadata as zero-margin files.

## Non-goals

- Controlling a Cast player, projector, screen, or any other device.
- Changing processing profiles or the existing intro/outro transitions and
  fades.
- Rewriting old compiled files merely to add margins; old files remain
  selectable and are described as having no margins.

## Design

### Worker configuration and cache identity

Add `lead_in_duration` and `tail_out_duration` numeric Worker add-on options,
both defaulting to `2.0` seconds and validated as non-negative finite values.
Pass them through `AppOptions` into immutable `WorkerSettings`. These global
Worker options are the calibration controls; Home Assistant does not configure
or interpret them.

Append both values to the compilation fingerprint material together with the
existing clip, source, and processing-profile identity. A change to either
value therefore makes previous outputs stale and queues a replacement under
the existing scan/recompile flow. Persist the chosen values in each compile
job snapshot so a queued job uses the configuration with which its fingerprint
was made, even if options change before it runs.

### FFmpeg composition and final-file metadata

Build black video and silent audio at the profile's output dimensions, frame
rate, sample rate, and channel layout. Place those streams before and after the
existing processed A/V result using non-overlapping stream composition, so
there is no crossfade into or over source content. Preserve the processed
content's timestamps and synchronize the leading silence with the leading
black frames; pad the tail on both streams. Existing profile-defined content
processing stays intact.

After FFmpeg completes, probe the temporary final file. Publish it atomically,
then probe or validate the published file before recording metadata so the
stored values describe the exact path served. Persist `output_duration_seconds`
from that probe and the following metadata alongside the output fingerprint:

- `content_duration_seconds`: duration of the processed content region.
- `lead_in_duration_seconds` and `tail_out_duration_seconds`: measured
  margins, bounded by the final file duration.
- `content_start_offset_seconds`: first content instant in the final file.
- `content_end_offset_seconds`: end of content and start of tail black.

Calculate offsets against the actual final duration and the encoded video
timeline (including frame-rate rounding), maintaining
`content_start_offset_seconds <= content_end_offset_seconds <= duration`.
Do not infer the total from source duration or only from requested pad values.
The final audio/video streams must cover the same timeline; validation rejects
an output where one stream is materially shorter than the other.

Catalog reads expose those optional output fields from persisted metadata. For
an existing file with no such metadata, output recovery measures its actual
duration and returns that as content duration, zero for both margins, start
offset zero, and end offset equal to actual duration. Missing or malformed
metadata must never be associated with a different file: output fingerprint
and file availability remain the authority for persisted values.

### Home Assistant selection response

Extend the Worker clip response model and the integration's `ClipAvailability`
with optional compiled timing metadata. On selection, return these additional
fields when a compiled duration is known:

```json
{
  "duration": 200.0,
  "content_duration": 180.0,
  "lead_in_duration": 10.0,
  "tail_out_duration": 10.0,
  "content_start_offset": 10.0,
  "content_end_offset": 190.0
}
```

All values are seconds and may be fractional. `duration` is the measured total
file duration; the other values describe content and margin boundaries.
Retain `duration_seconds` with its current meaning and behavior for existing
consumers. If a selected file has no recorded margin metadata, return the
legacy-compatible timing interpretation: `duration` and `content_duration`
equal the measured output duration, both margins are zero, and content
occupies `[0, duration]`. When duration cannot be measured, keep the current
unknown-duration behavior and do not fabricate offsets.

`select_next_clip` continues to return media identity/URI and descriptive
metadata only. It performs no playback or device-control calls.

### Contract and documentation

Document the Worker catalog's optional compiled timing metadata and the
selection response's new fields in the OpenAPI contract, service documentation,
and user-facing configuration/API docs. Show that Home Assistant can use
`content_start_offset` and `content_end_offset` for display timing, while
Cinema Collections only prepares the file and its metadata.

## Error handling and compatibility

- Invalid negative, non-finite, or unsupported margin values fail Worker
  settings validation at startup with the existing configuration error path.
- FFmpeg failure or final-file timing validation failure must leave the prior
  published output intact and must not commit metadata for a partially
  generated file.
- Existing HA consumers continue to receive `duration_seconds` unchanged.
- Existing Worker clients ignore the newly optional catalog fields, and
  existing catalog records without margins follow the zero-margin recovery
  rules.

## Verification

Cover configuration defaults/validation, fingerprint changes, FFmpeg graph
construction for synchronized black/silence at both ends, persistence and
recovery of timing metadata, and Home Assistant selection serialization for
new and legacy outputs. Update contract checks and documentation examples.
Inspect a generated media fixture with ffprobe to confirm that total duration
comes from the final file and that audio/video timelines remain synchronized.
