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
  - entity: button.cinema_collections_scan_library
  - entity: button.cinema_collections_compile_all_collections
  - entity: button.cinema_collections_cancel_processing
```

Entity IDs are examples; use the IDs created by your own Home Assistant instance. The active-collection sensor includes reason, override state, queue depth, progress, compatibility, and a sanitized Worker error attribute. A Worker outage is reported as unavailable rather than idle.

For an automation, first adopt the `select_next_clip` service response manually in Developer Tools. Its response includes `media_content_id`, `media_content_type`, collection ID, and clip ID. After confirming that value with your existing media-player action, insert only the response value into the selection part of that automation. Retain all Cast, projector, screen, music, timing, and cleanup actions exactly where they already are.

```yaml
service: cinema_collections.select_next_clip
data:
  dry_run: true
response_variable: next_clip
```

Remove `dry_run` only after checking the returned URI. The service selects media; it does not play it.
