# Architecture

Cinema Collections is deliberately split into two processes. The Home Assistant integration owns collection policy, schedules, overrides, playback history, native entities, and automation services. The Cinema Collections Worker owns the catalog, processing profiles, FFmpeg/ffprobe work, queue, logs, and the Ingress Library Manager.

The Library Manager is the Worker's operational UI, reached through App Ingress: it imports source clips (in fixed-size chunks) and scans for files already on disk, recompiles or trashes individual catalog items, edits per-clip tags and notes, shows and copies stable clip IDs, edits a collection's visual playback order, uploads and deletes intro/outro assets, shows live job progress plus recent-jobs and Worker-log history, and permanently deletes one explicitly selected catalog item after confirmation.

The playback-order editor derives its catalog from the Worker's existing clip rows and saves through the integration's authenticated bridge (`/api/cinema_collections/order`), the only absolute, same-origin URL the page uses; every Worker route stays relative so App Ingress keeps working. The integration remains authoritative for `playback_mode` and `ordered_clip_ids` — the Worker persists no order — and when the bridge is unreachable the editor degrades to copying IDs for manual entry.

The integration communicates only with the authenticated versioned Worker API. It never runs FFmpeg or ffprobe, reads arbitrary media files, or controls a media player, Cast device, projector, screen, music system, or any other physical device. Existing playback automations remain the device-control layer.

The Worker accepts only typed processing-profile fields and paths resolved under configured allowlisted roots. It writes temporary output under its data area, validates output before publishing, and publishes atomically. Worker loss makes operational entities unavailable but does not erase local collection policy or playback history.

## Responsibilities and data flow

1. Create collections and profiles with the Worker and retain Home Assistant policy in config-entry subentries.
2. The Worker scans source roots, catalogs clips, and compiles only requested work.
3. The integration polls `/health` and `/status`, exposing state through sensors and controls through buttons and services.
4. An automation calls `cinema_collections.select_next_clip`; the response contains a Media Source URI for the existing playback automation to adopt.

Diagnostics contain bounded snapshots of this state. They remove bearer credentials and absolute paths, while preserving compatibility, queue, and sanitized error information for support.
