# Troubleshooting

Each entry lists the symptom, why it happens, and what to do. Error text is
quoted literally where it exists.

## `Missing option 'bearer_secret' in root`

**Symptom.** The App will not start; the log reports
`Missing option 'bearer_secret' in root`.

**Cause.** `bearer_secret` has no default by design, so the App cannot start
without an explicit value.

**Fix.** Open the App's **Configuration** tab, set `bearer_secret` to a random
value of at least 43 characters with at least 16 distinct characters (for
example `openssl rand -base64 48`), then start the App again.

## `Cannot connect to the Worker`

**Symptom.** The pairing dialog shows **Cannot connect to the Worker.** after
you submit the endpoint.

**Cause.** The endpoint does not reach the Worker's private HTTP listener. The
most common cause is an endpoint built from the wrong hostname or from
`bind_host`/`bind_port` taken literally.

**Fix.** Read the real hostname in **Settings → Add-ons → Cinema Collections
Worker → Info tab**, in the **Hostname** field. Enter the endpoint as
`http://<hostname>:8099`, replacing `<hostname>` with that value and `8099` with
the App's configured `bind_port` if you changed it. `bind_host` and `bind_port`
in the App options are where the Worker listens; they are not the address Home
Assistant uses to reach it (in particular, do not use `bind_host` such as
`0.0.0.0` as the host part). Confirm the bearer token matches the App's
`bearer_secret` exactly.

## `413 Content Too Large`

**Symptom.** Uploading a clip through the Library Manager fails with
**413 Content Too Large**.

**Cause.** A reverse proxy or ingress hop in front of Home Assistant caps the
request body size. Cinema Collections imposes no request-body limit of its own
(individual uploads are capped at 2 GiB per file by the Worker).

**Fix.** This is already handled for the upload path: the Library Manager sends
files in fixed-size chunks (8 MiB) through the chunked upload endpoints, so
individual requests stay below proxy body limits, and the server streams each
chunk without buffering a whole file in memory. If a proxy still rejects the
request, or your library is large, skip HTTP upload entirely and use **Scan for
files already on disk**: copy the files into the collection's source directory
and scan by collection ID.

## `422 Unprocessable Content` from Scan library

**Symptom.** Scanning in the Library Manager fails with
**422 Unprocessable Content**.

**Cause.** A folder path was entered where the collection ID belongs. The scan
form accepts a collection's short ID, not a path such as
`/media/cinema-collections/source/regular`.

**Fix.** Enter the collection's short ID from the clip table (for example
`regular`), or leave the field empty to scan every collection.

## Library Manager looks unstyled, or changes do not appear after an update

**Symptom.** The Library Manager renders without styles, or edits made to
`manager.html`/`manager.css` do not show up.

**Cause.** Home Assistant's frontend service worker can serve a cached copy of
the Library Manager page.

**Fix.** Clear the service worker: open the browser's **DevTools →
Application → Service Workers → Unregister**, then **Clear site data** (or a
hard reload). Ingress pages are served with `no-store`, so this is only needed
after the frontend cached an earlier version.

## HACS shows both `worker-*` and `integration-*` versions

**Symptom.** HACS offers both `worker-v1.x.y` and `integration-v1.x.y`
releases for the same repository.

**Cause.** This is a monorepo with two separately installable components, and
HACS lists every release tag.

**Fix.** Pick an `integration-vX.Y.Z` tag in HACS for the integration. Tags
named `worker-vX.Y.Z` belong to the App, which updates through the Supervisor,
not through HACS.

## A clip shows state `failed` or `invalid`

**Symptom.** A clip row in the Library Manager shows **failed** or **invalid**.

**Cause.** Compilation could not produce a valid output, or the input could not
be probed.

**Fix.** Look at the failure reason shown directly in the clip row, then the
Worker log section in the Library Manager for the surrounding messages, and the
**Recent jobs** section for the job's recorded error. The integration's
**Latest Worker error** sensor (`latest_error`) also surfaces the most recent
error for dashboards. Fix the underlying cause (for example a profile setting,
a corrupt source file, or a missing asset) and use the clip's **Recompile**
action, or the `retry_failed` service.

## Compiled output does not appear in the media browser

**Symptom.** Compiled clips are produced, but they do not show up in Home
Assistant's media browser.

**Cause.** The compiled root must be reachable at the media-source URI the
integration was paired with. The integration builds its `media_content_id`
URIs from the configured `media_uri_prefix`, which defaults to
`media-source://media_source/local/cinema-collections/compiled`.

**Fix.** Make sure Home Assistant's local media directory maps to the Worker's
`compiled_root` (`/media/cinema-collections/compiled` by default) at that
prefix, for example through an external media directory. The URI prefix and the
compiled root must refer to the same directory.

## Before reporting a problem

Gather diagnostics first:

- Download the integration's **Diagnostics** from the integration entry page.
  It omits secret fields, strips authorization text, replaces known Worker root
  paths with their labels, redacts any other absolute path, and limits error
  excerpts, while retaining compatibility versions and bounded queue/progress
  state for support.
- Open the App's **Log** tab for its startup and runtime messages.
- Open the Library Manager's **Worker log** section for the most recent 200
  Worker entries, and the **Recent jobs** section for the most recent 50 jobs.