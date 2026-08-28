# Configuration

## Worker settings

Set a unique random `bearer_secret` before first use and rotate it by updating both the Worker and the integration config entry. The Worker endpoint is an HTTP(S) base URL with no embedded credentials or path. The Worker refuses public or short static bearer tokens.

`source_root` and `compiled_root` must both be distinct descendants of the trusted media root. The Worker rejects absolute user-supplied clip paths, traversal paths, and paths outside those roots. Its data directory holds the database, bounded logs, temporary processing files, and recoverable Library Manager trash; back it up with the database.

Set `disk_reserve_bytes` to retain free space for Home Assistant and the host. Hardware acceleration is disabled by default. Processing profiles are typed settings, not shell commands or raw FFmpeg filters. Start with the Compatibility 4K Loudness Profile, then create a named profile for intentional changes.

## Collections and policy

Use the integration’s config subentries to create collections and select their Worker profile. Each collection has an ID, enabled state, priority, optional default designation, schedules, and a manual-override permission. The global Options flow chooses automatic, default, or explicit selection. The Select entity exposes automatic/default plus only enabled collections that permit manual override.

The global Options flow also configures the daily history-reset time (`history_reset_time`) and whether local subentry revisions are re-synchronized to the Worker at startup (`sync_on_startup`). The Config flow records the compiled media-source directory (`media_uri_prefix`) used to build `select_next_clip` media-content URIs; the default maps Home Assistant local media at `media-source://media_source/local/cinema-collections/compiled`, so mount the Worker compiled root there (for example through an external media directory).

The Worker Library Manager is for importing, organizing, inspecting, scanning, recompiling, and recovering selected catalog items. It never permits arbitrary host paths. Deleting source and deleting compiled output are separate actions; permanent deletion is selected-item-only and requires a second confirmation.
