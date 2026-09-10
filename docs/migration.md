# Migration and rollback

## Moving configuration into the Worker

Integration 2.0.0 moves collections and processing profiles out of the Home
Assistant config entry and into the Worker, which executes them. Playback order
and collection schedule times move with them.

**Take a backup first.** Back up Home Assistant before updating, so the config
entry's contents are recoverable.

The first time the updated integration starts, it migrates the entry once:

1. It pushes every collection and profile subentry to the Worker.
2. It reads them back and compares each record field by field.
3. Only on a full match does it remove the subentries and mark the entry
   migrated.

Anything else — an unreachable Worker, a rejected record, a value the Worker
stored differently — leaves the configuration in Home Assistant untouched and
retries on the next startup. Every pushed payload is written to the log at info
level before anything is deleted, so it can be recovered from the log if needed.

The integration requires Worker 1.8.0 or newer. Against an older Worker it
refuses to set up and names the version to install, because an older Worker
rejects the policy fields rather than storing them.

After migrating, collections and profiles are edited in the Library Manager's
**Collections** section. The Home Assistant forms are gone, and so is the
`/api/cinema_collections/order` bridge the playback-order editor used.

## Observation mode

Start with a new Worker namespace and separate source/compiled directories. Configure the integration, create equivalent collections and profiles, scan and compile copies of a small representative library, and add the dashboard cards. Leave all existing helpers and automations untouched.

Run `cinema_collections.select_next_clip` with `dry_run: true` and compare collection choice and generated Media Source URI with the current workflow. Review Worker errors, output availability, profile behavior, disk reserve, and schedules. This is observation mode: the system reports and selects metadata only; no device should be affected.

## Explicit adoption

After observation succeeds, use Developer Tools to call `select_next_clip` without `dry_run` and inspect its response. Only with the owner’s separate explicit approval, replace the *selection* expression in one chosen playback automation with the returned `media_content_id`. Keep that automation’s device actions and helpers unchanged. Adopt one automation at a time and retain the original selection configuration until it has run successfully.

## Rollback

To roll back, restore the prior selection expression or disable the one updated service call. You may disable the integration or stop the Worker without deleting media; this does not modify existing automations, helpers, playlists, or devices. Do not use permanent deletion as rollback. Worker-managed trash is recoverable and is not automatically purged.

## Backups and upgrades

Back up the Worker data directory (including its SQLite database, audit state, and recoverable trash) and source media before upgrades. Compiled output can be regenerated but is still worth retaining until validation is complete. Upgrade the Worker and integration together when compatibility reports require it, then confirm `/health`, queue state, and a dry-run selection before resuming scheduled compilation. Keep the previous image/version available until the validation pass is complete.
