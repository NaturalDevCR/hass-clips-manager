# Configuration

## Worker settings

Set a unique random `bearer_secret` before first use and rotate it by updating
both the Worker and the integration config entry. The Worker endpoint is an
HTTP(S) base URL with no embedded credentials or path. The Worker rejects
public or short static bearer tokens: the secret must be at least 43 characters
and contain at least 16 distinct characters.

`source_root` and `compiled_root` must both be distinct descendants of the
trusted media root; the defaults are `/media/cinema-collections/source` and
`/media/cinema-collections/compiled`. The Worker rejects absolute user-supplied
clip paths, traversal paths, and paths outside those roots. Its data directory
holds the database, bounded logs, temporary processing files, recoverable
Library Manager trash, and uploaded intro/outro assets; back it up with the
database.

Set `disk_reserve_bytes` to retain free space for Home Assistant and the host.
Hardware acceleration is disabled by default. Processing profiles are typed
settings, not shell commands or raw FFmpeg filters. Start with the Compatibility
4K Loudness Profile, then create a named profile for intentional changes.

## Pairing and global options

The integration config flow stores three values: `endpoint` (**Worker
endpoint**), `token` (**Bearer token**), and `media_uri_prefix` (**Compiled
media source directory URI**). The last one defaults to
`media-source://media_source/local/cinema-collections/compiled` and is the
Media Source directory under which `select_next_clip` builds its
`media_content_id` URIs. The Worker's compiled root must be reachable at that
path (for example through an external media directory).

The global **Options** flow exposes collection-selection policy without YAML:

- `override_mode` (**Selection mode**) — `automatic`, `default`, or `explicit`.
- `override_collection_id` (**Collection for explicit mode**) — required only
  when `override_mode` is `explicit`.
- `history_reset_mode` (**History reset mode**) — `on_exhaustion` (default) or
  `daily`. In `on_exhaustion` mode a collection's no-repeat history only rolls
  over once every eligible clip in it has been played; there is no time-based
  wipe. In `daily` mode that exhaustion rollover still applies, and every
  collection's history is additionally wiped at `history_reset_time` each local
  day. A manual reset (the `reset_history` service or button) works in both
  modes.
- `history_reset_time` (**Daily history reset time**) — the local time at which
  every collection's no-repeat playback history resets; default `00:00`,
  minute precision only. Applies only when `history_reset_mode` is `daily`.
- `sync_on_startup` (**Synchronize configuration on startup**) — whether local
  subentry revisions are re-synchronized to the Worker at startup; default
  enabled.

## Collection subentries

Each collection is a **Collection** config subentry on the integration entry.
Creating or editing one synchronizes the Worker, which rejects values it cannot
accept and returns the error in the form. The fields are:

| Field | Meaning |
| --- | --- |
| Collection ID | Immutable lowercase URL-safe slug (`^[a-z0-9]+(?:-[a-z0-9]+)*$`). |
| Name | Display name. |
| Source directory | Directory **relative to `source_root`**, for example `regular` means `<source_root>/regular`. Never an absolute path. |
| Processing profile ID | The profile used to compile this collection's clips. |
| Enabled | Whether the collection participates in policy resolution and compilation. |
| Priority | Higher-priority collections win when several are scheduled; higher values win. |
| Starts at / Ends at | Optional timezone-aware window during which the collection is eligible for automatic selection. |
| Default collection | Marks the collection as the fallback when no collection is scheduled. |
| Allow manual override | Whether the collection can be chosen through the Select entity or `set_collection_override`. |
| Tags | Comma-separated metadata tags. |
| Notes | Free-form notes. |
| Enable schedule / Schedule weekdays / Schedule time / Schedule strategy / Skip schedule while processing | Optional local recurring compilation schedule. Weekdays use Python weekday values 0 (Monday) through 6 (Sunday). |

## Processing profile editor

The **Processing profile** config subentry is a field-by-field form, not a raw
JSON textarea. It groups the settings that were previously edited as JSON into
typed fields with per-field bounds. Submitting validates the profile, and the
Worker rejects invalid combinations such as a `required` audio missing policy
paired with a `silence` fallback.

See [processing-profiles.md](processing-profiles.md) for the complete field
reference, defaults, and validation rules.

## Library Manager

The Worker Library Manager is for importing (in 8 MiB chunks), scanning, and
recompiling clips; editing per-clip tags and notes; renaming or moving clips
within their collection; creating collection subfolders; uploading and deleting
intro/outro assets; and recovering or permanently deleting selected catalog
items. It never permits arbitrary host paths. Deleting source and deleting
compiled output are separate actions; permanent deletion is selected-item-only
and requires a second confirmation. Asset deletion is refused while a
processing profile still references the asset.