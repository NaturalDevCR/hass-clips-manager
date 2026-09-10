# The Worker owns collections and processing profiles

## Problem

Collections and processing profiles are stored in the Home Assistant config
entry as subentries, and pushed to the Worker on every edit and again on every
startup (`_async_sync_subentries_on_startup`). Home Assistant is the source of
truth; the Worker's `collections` and `profiles` tables are a replica of it.

That inverts the natural ownership. Only the Worker executes this
configuration — it scans, compiles, and serves clips — but it may not decide
anything about it. The cost shows up in three places:

- `custom_components/cinema_collections/subentries.py` is 1181 lines, most of it
  a Home Assistant form mirroring the Worker's typed processing profile field by
  field, plus 454 lines of translations per language labelling those fields. The
  Worker already validates the same profile in `profile_validation.py`, so every
  constraint exists twice.
- Playback order lives in the config entry with the rest of the collection
  policy, so the Worker's own order editor cannot write it directly. That is the
  only reason `order_view.py` and the signed, five-minute
  `order_bridge_capability` exist.
- The config entry is used as a content store rather than as the integration's
  own configuration, which is what a config entry is for.

Now that the Library Manager is a real interface, editing this configuration in
Home Assistant has no remaining advantage.

## Goals

- Make the Worker the source of truth for collections and processing profiles.
- Edit both in the Library Manager.
- Reduce the integration to what Home Assistant is uniquely good at: exposing
  entities, services, and a schedule to automations.
- Migrate existing installations without losing configuration and without
  changing any entity's identity.

## Non-goals

- Changing selection, compilation, or playback behavior.
- Moving the compilation schedule's execution out of Home Assistant.
- Any change to how clips themselves are catalogued.

## The ownership split after this change

| Concern | Owner |
| --- | --- |
| Collections, profiles, playback order, schedule times | Worker |
| Connection settings (host, port, bearer secret) | Integration config entry |
| Entities: sensors, buttons, collection-override select | Integration |
| Services, including `select_next_clip` | Integration |
| Schedule dispatch (clock, timezone, durable run tokens) | Integration |

The schedule is the one deliberate split: its *data* is collection policy and
moves to the Worker, while its *execution* stays in `scheduler.py`, which
already owns a clock, a timezone, and durable occurrence state. Duplicating that
in the Worker would rewrite working code to no benefit.

## Worker changes

### Collection model

The Worker's collection currently holds `id`, `name`, `source_directory`,
`processing_profile_id`, `enabled`, `priority`, `is_default`,
`allow_manual_override`, `tags`, and `notes`. Five fields that exist only in
Home Assistant today move in:

| Field | Type | Meaning |
| --- | --- | --- |
| `starts_at` | ISO 8601 string or null | Start of the collection's active window |
| `ends_at` | ISO 8601 string or null | End of that window |
| `schedule` | object | `enabled`, `local_time`, and the rest of the existing schedule mapping |
| `playback_mode` | `random` \| `sequential` \| `custom` | How the integration picks the next clip |
| `ordered_clip_ids` | list of clip ids | The custom order, required when `playback_mode` is `custom` |

They are added to the `collections` table by migration, to `CollectionCreate`,
`CollectionPatch`, and `CollectionRecord`, and to `contract/openapi-v1.yaml`.

The additions are backward compatible for readers: a client that ignores the new
fields behaves as before, so `/api/v1` keeps its version. `CollectionCreate` and
`CollectionPatch` forbid unknown fields, so an older Worker paired with a newer
integration rejects the new fields outright rather than silently dropping them —
the integration therefore requires a Worker at or above this version, and says
so when it is not.

The invariant that `playback_mode: custom` requires a non-empty
`ordered_clip_ids` moves with the data and is enforced by the Worker.

### Library Manager

A new **Collections** section holds two editors:

- **Collection.** Identity and paths, the processing profile, priority and
  default, the active window and schedule, the playback mode, and tags and
  notes. The playback-order editor moves inside this section as the mode's own
  control, and saves to the Worker like every other field.
- **Processing profile.** The typed profile as its actual shape rather than the
  flattened form the Home Assistant version had to use: video, audio, loudness,
  scaling, and transition, each with its mode switch driving which fields apply.
  Intro and outro pick from the assets the Worker already lists.

The Worker validates on save and returns its refusal, so the interface reports
what `profile_validation.py` says instead of restating its rules.

The order bridge is deleted: `_order_bridge_capability`, the capability header,
its five-minute expiry, and the interface's Copy IDs fallback all go, because
saving an order is now an ordinary authenticated write.

## Integration changes

Deleted: `subentries.py`, `order_view.py`, the collection and profile subentry
types, and the translation strings for their forms.

Kept and re-pointed at the Worker: `coordinator.py` already fetches from the
Worker, and becomes the single source for collection policy. `scheduler.py`,
`selection.py`, and `resolver.py` read policy from the coordinator's snapshot
rather than from subentries.

With the Worker unreachable, entities report unavailable. Nothing is cached: a
stale copy of the configuration would reintroduce the duplication this change
removes, and no useful action is possible while the Worker is down anyway.

## Migration

One way, on startup, once per entry.

1. If the entry has no collection or profile subentries, or is already marked
   migrated, do nothing.
2. Push every subentry to the Worker, exactly as
   `_async_sync_subentries_on_startup` does now, carrying the five new fields.
3. **Read the collections and profiles back from the Worker** and compare them
   against what was sent.
4. Only if every record matches, mark the entry migrated and remove the
   subentries.
5. If anything fails — the Worker is unreachable, rejects a record, or returns
   something that does not match — remove nothing, log what failed, and try
   again on the next startup.

The read-back in step 3 is the point of the design. Deleting the only copy of a
user's configuration on the strength of a 2xx would lose it whenever the Worker
accepted a write it did not durably store.

Migration runs before the platforms are set up, so entities never observe the
half-migrated state.

## Compatibility

This is a breaking change for the integration: a major version, released
alongside a Worker that has the new fields. The integration checks the Worker's
version at setup and reports a clear error when it is too old, rather than
failing later on a rejected field.

Entity identity does not change. Sensors, buttons, and the select are keyed on
`entry_id` plus a description key, never on a subentry, so no `unique_id`
changes and no dashboard or automation breaks.

## Testing

- Worker: the five new fields round-trip through create, patch, and read; the
  custom-order invariant is enforced; the migration adds the columns to an
  existing database.
- Interface: the collection and profile editors save and report the Worker's
  refusals; the order editor writes through the ordinary route.
- Integration: migration pushes, reads back, and only then deletes; a failed
  read-back leaves the subentries intact; a second startup is a no-op; the
  scheduler and selection read policy from the coordinator snapshot.
- Contract: the roundtrip test covers a collection carrying schedule and order.

## Accepted risk

The migration deletes configuration from the config entry after verifying it in
the Worker. If the Worker acknowledges and returns a record that later vanishes
— a restore from an older Worker backup, say — that configuration is gone from
Home Assistant too. The release notes tell users to take a backup, and the
migration logs the full pushed payload before deleting anything, so it can be
recovered from the log.
