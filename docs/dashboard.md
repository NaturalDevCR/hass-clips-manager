# Dashboard

Use standard Lovelace cards; Cinema Collections intentionally ships no custom card. The integration is observation and selection only: these cards do not start playback or control devices.

```yaml
type: entities
title: Cinema Collections
entities:
  - entity: sensor.cinema_collections_active_collection
    name: Active collection
  - entity: select.cinema_collections_collection_override
  - entity: sensor.cinema_collections_processing_status
  - entity: sensor.cinema_collections_queue_depth
  - entity: sensor.cinema_collections_current_job_progress
  - entity: sensor.cinema_collections_latest_worker_error
  - entity: sensor.cinema_collections_worker_version
  - entity: sensor.cinema_collections_next_compilation_schedule
  - entity: sensor.cinema_collections_collection_priorities
  - entity: sensor.cinema_collections_compilation_summary
  - entity: sensor.cinema_collections_clip_states
  - entity: button.cinema_collections_scan_library
  - entity: button.cinema_collections_compile_all_collections
  - entity: button.cinema_collections_retry_failed_compilation
  - entity: button.cinema_collections_cancel_processing
  - entity: button.cinema_collections_clean_up_temporary_files
  - entity: button.cinema_collections_reset_playback_history
```

Entity IDs are examples that follow the entity translation names; use the IDs
created by your own Home Assistant instance.

## Sensors

| Sensor | State | Notes |
| --- | --- | --- |
| Active collection | active collection ID | Includes `reason`, `override_rejected`, `queue_depth`, `progress`, `compatibility`, `worker_error`, `next_schedule`, `collection_priorities`, `compilation_summary`, `clip_states`, `current_job`, and `history` attributes. |
| Processing status | `unavailable`, `idle`, or the current job state | Reports `unavailable` during a Worker outage rather than idle. |
| Queue depth | queued jobs | Count, with `progress` and `current_job` attributes. |
| Current job progress | percentage | The same progress the Library Manager shows for the active job. |
| Latest Worker error | error message | Bounded, sanitized Worker error text. |
| Worker version | version string | From the Worker health response. |
| Next compilation schedule | ISO timestamp | `null` when no enabled schedule exists. |
| Collection priorities | number of collections | A `collection_priorities` attribute maps each collection ID to its priority. |
| Compilation summary | `ready/total ready` | A compact ready-count summary across the catalog. |
| Clip states | total catalogued clips | A `clip_states` attribute counts clips by state (for example `discovered`, `stale`, `ready`, `failed`, `invalid`). |

## Playback history attribute

The active-collection sensor exposes a `history` attribute with one entry per
collection. Each entry records `round_number`, `played_count`,
`last_selected_clip_id`, and `last_reset_at`. `last_reset_at` reflects whichever
reset actually occurred: the daily wipe when `history_reset_mode` is `daily`, a
manual reset in either mode, or `null` when only exhaustion rollovers have
happened (a rollover is visible through a rising `round_number`, not
`last_reset_at`). A template card can read it:

```yaml
type: markdown
content: >-
  {% set history = state_attr('sensor.cinema_collections_active_collection', 'history') %}
  {% for collection_id, record in (history or {}).items() %}
  - **{{ collection_id }}**: round {{ record.round_number }},
    {{ record.played_count }} played, last **{{ record.last_selected_clip_id }}**
  {% endfor %}
```

## Automation

For an automation, first adopt the `select_next_clip` service response manually in Developer Tools. Its response includes `media_content_id`, `media_content_type`, `collection_id`, `clip_id`, `relative_output_path`, `duration_seconds`, and `history_reset`. After confirming that value with your existing media-player action, insert only the response value into the selection part of that automation. Retain all Cast, projector, screen, music, timing, and cleanup actions exactly where they already are.

```yaml
service: cinema_collections.select_next_clip
data:
  dry_run: true
response_variable: next_clip
```

Remove `dry_run` only after checking the returned URI. The service selects media; it does not play it.