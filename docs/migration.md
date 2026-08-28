# Migration and rollback

## Observation mode

Start with a new Worker namespace and separate source/compiled directories. Configure the integration, create equivalent collections and profiles, scan and compile copies of a small representative library, and add the dashboard cards. Leave all existing helpers and automations untouched.

Run `cinema_collections.select_next_clip` with `dry_run: true` and compare collection choice and generated Media Source URI with the current workflow. Review Worker errors, output availability, profile behavior, disk reserve, and schedules. This is observation mode: the system reports and selects metadata only; no device should be affected.

## Explicit adoption

After observation succeeds, use Developer Tools to call `select_next_clip` without `dry_run` and inspect its response. Only with the owner’s separate explicit approval, replace the *selection* expression in one chosen playback automation with the returned `media_content_id`. Keep that automation’s device actions and helpers unchanged. Adopt one automation at a time and retain the original selection configuration until it has run successfully.

## Rollback

To roll back, restore the prior selection expression or disable the one updated service call. You may disable the integration or stop the Worker without deleting media; this does not modify existing automations, helpers, playlists, or devices. Do not use permanent deletion as rollback. Worker-managed trash is recoverable and is not automatically purged.

## Backups and upgrades

Back up the Worker data directory (including its SQLite database, audit state, and recoverable trash) and source media before upgrades. Compiled output can be regenerated but is still worth retaining until validation is complete. Upgrade the Worker and integration together when compatibility reports require it, then confirm `/health`, queue state, and a dry-run selection before resuming scheduled compilation. Keep the previous image/version available until the validation pass is complete.
