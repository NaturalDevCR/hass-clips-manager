# Visual Playback Order Design

## Goal

Make clip ordering usable without exposing opaque UUIDs as the primary user
interface. The Worker Library Manager will display stable clip IDs, provide
copy actions, and offer an accessible drag-and-drop ordering editor. Saving an
order will update the Home Assistant integration's collection policy through a
same-origin authenticated bridge when the App is opened through Home Assistant.

## Ownership and data flow

- The Worker remains the source of catalog data, clip metadata, and the
  Library Manager UI.
- The integration remains authoritative for playback mode and ordered clip IDs.
- The integration exposes `POST /api/cinema_collections/order`, authenticated
  by the Home Assistant user session. It validates the collection and stores
  the ordered stable IDs in the collection subentry.
- The Manager calls that bridge only through a same-origin absolute URL. All
  Worker routes remain relative so App Ingress continues to work.
- When the bridge is unavailable (external Docker or a non-HA browser), the
  UI still supports copying the ordered IDs for manual configuration.

## Ordering UX

- Library table: full clip ID column and per-row Copy ID action.
- Playback order tab: collection selector, rows with source name, duration,
  state, and full ID, drag handle, Copy IDs, Save order, and Reset to path
  order.
- The editor includes catalogued clips even when not currently playable, with
  state shown. Selection safely skips unavailable IDs as before.
- Initial order uses the saved custom order when the Home Assistant bridge is
  available; remaining clips use the existing deterministic path order.

## Security and failure behavior

- The bridge requires an authenticated Home Assistant user and never accepts
  filesystem paths or Worker credentials.
- IDs are validated as non-empty, unique strings using the integration's
  existing normalization rules.
- Unknown or unavailable IDs may remain in the saved list so a temporary scan
  or compile state does not destroy editorial intent; playback continues to
  skip them safely.
- A failed bridge save leaves the local drag-and-drop state intact and offers
  a copy fallback; it never silently reports success.

## Compatibility

Existing random and sequential modes are unchanged. Existing collections keep
their current mode and history. Worker API v1 and database schema do not
change.
