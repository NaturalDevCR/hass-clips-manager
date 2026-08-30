# Getting started

This walkthrough takes you from an empty Home Assistant installation to a
compiled clip selected by an automation. It assumes the supervised
Home Assistant App deployment. External Docker users can follow the same
steps after installing the Worker image (see [installation.md](installation.md)).

You will install the **Cinema Collections Worker** App, install the
**Cinema Collections** integration through HACS, pair the two, configure one
collection and its processing profile, compile a clip, and adopt the result in
an automation.

## 1. Install the Worker App

1. In Home Assistant, open **Settings → Add-ons → Add-on Store**.
2. Open the three-dot menu (**⋮**) and choose **Repositories**.
3. Add the repository root URL:

   ```text
   https://github.com/NaturalDevCR/hass-clips-manager
   ```

   Use the plain repository root. Do not paste a URL that contains
   `/tree/main/...`; the Supervisor cannot clone a tree URL.
4. Reload the store, find **Cinema Collections Worker**, and install it.

The repository is declared as an App repository, so the App updates through the
Supervisor. Do not use HACS to update it.

## 2. Set the bearer secret

`bearer_secret` has no default, by design. The App refuses to start without it,
and the Worker rejects weak or short values: the secret must be at least 43
characters long, contain at least 16 distinct characters, and must not be a
known placeholder such as `change-me-before-starting`.

Generate a suitable value:

```sh
openssl rand -base64 48
```

Paste the result into the App's **Configuration** tab under `bearer_secret`
before the first start.

Treat the value as a password. Keep it out of URLs, logs, dashboards, and
automation YAML. The integration's pairing dialog asks for the same value in
step 6.

## 3. Set the media roots

In the App's **Configuration** tab, set:

- `source_root`, default `/media/cinema-collections/source`
- `compiled_root`, default `/media/cinema-collections/compiled`

Both must be distinct directories under the App's `/media` mount. The Worker
rejects overlapping roots. The Worker reads source clips from `source_root` and
writes compiled clips to `compiled_root`.

## 4. Start the App and open the Library Manager

Start **Cinema Collections Worker** from the Add-ons page. Once it is running,
open **Settings → Add-ons → Cinema Collections Worker → Open web UI**. This
opens the **Library Manager** through App Ingress. You will use it in steps 9
through 11.

## 5. Install the integration

1. In Home Assistant, open **HACS → ⋮ → Custom repositories**.
2. Add the same repository root URL, with the category **Integration**.
3. HACS lists every release tag in this repository, which is a monorepo. Pick a
   tag named `integration-vX.Y.Z` — those tags are the integration. Tags named
   `worker-vX.Y.Z` belong to the App, which updates through the Supervisor, not
   through HACS.
4. Install **Cinema Collections** from HACS and restart Home Assistant.
5. Add the integration from **Settings → Devices & services → Add
   integration**, and choose **Cinema Collections**.

## 6. Pair the integration

The pairing dialog has three fields: **Worker endpoint**, **Bearer token**, and
**Compiled media source directory URI**.

1. Find the Worker's private hostname in **Settings → Add-ons → Cinema
   Collections Worker → Info tab**, in the **Hostname** field.
2. Enter the endpoint as:

   ```text
   http://<hostname>:8099
   ```

   Replace `<hostname>` with the value from the Info tab. `8099` is the default
   `bind_port` in the App's `config.yaml`; change it to match if you configured
   a different port. The integration reaches the Worker over the App's private
   address, so it must be the App hostname, not `bind_host` as a literal
   address.
3. Enter the same bearer token from step 2 in **Bearer token**.
4. Leave **Compiled media source directory URI** at its default:

   ```text
   media-source://media_source/local/cinema-collections/compiled
   ```

   This maps Home Assistant's local media directory to the Worker's
   `compiled_root` (configured in step 3). The `select_next_clip` service builds
   its `media_content_id` from this prefix, so the compiled root must be
   reachable at that Media Source path for a selection to play.

The flow verifies authentication and API compatibility before it completes.

## 7. Create a processing profile

A profile named **Compatibility 4K Loudness Profile** (ID
`compatibility-4k-loudness`) is seeded into the Worker automatically when the
App starts. It is a complete, editable baseline (3840x2160 at 24 fps, AAC audio,
two-pass loudness normalization).

You only need to create a new profile to change those settings. To do so, open
the integration entry in **Settings → Devices & services**, and add a
**Processing profile** config subentry. The editor is a field-by-field form,
not a JSON textarea. See [processing-profiles.md](processing-profiles.md) for
the field reference.

## 8. Create a collection

Add a **Collection** config subentry from the same integration entry page.

Two fields cause most setup mistakes, so read them together:

- **Collection ID** is a short lowercase slug, for example `regular`. It must
  match the pattern `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
- **Source directory** is a path **relative to `source_root`**. The value
  `regular` means `<source_root>/regular`, that is
  `/media/cinema-collections/source/regular`. Never enter an absolute path such
  as `/media/cinema-collections/source/regular`.

The other collection fields control name, enabled state, priority, optional
default designation, manual-override permission, tags, notes, and the optional
compilation schedule. Pick the processing profile from step 7 in **Processing
profile ID**.

## 9. Get clips in

You have two ways to add source clips to the collection:

- **Upload through the Library Manager.** In the **Import source clips** form,
  enter the collection's short ID and select one or more video files. The
  manager sends each file as fixed-size chunks (8 MiB) through the chunked
  upload endpoints, so requests stay small enough to pass through reverse
  proxies that cap request-body size. The server stages chunks and never
  buffers a whole file in memory.
- **Scan for files already on disk.** Place files under the collection's source
  directory by any means — the Home Assistant Files add-on, Samba, a mounted
  network share — then use the **Scan for files already on disk** form in the
  Library Manager with the collection's short ID. Use the collection ID, not a
  folder path.

Scanning avoids browser upload size limits entirely. Prefer it for large
libraries or when a reverse proxy in front of Home Assistant caps request
bodies.

## 10. Add intro and outro assets

Intro and outro files are uploaded, not dropped in a folder. The assets root
lives in the Worker's private storage (`/data/assets`), not under `/media`, so
you cannot reach it through a media share.

In the Library Manager's **Intro/outro assets** section, upload the files, then
reference the exact filename shown in the list in the processing profile's
**Intro asset filename** and **Outro asset filename** fields. The profile
editor's intro and outro fields are dropdowns populated from the uploaded
assets, with an explicit **None** choice; free-text entry is still allowed.

Intro and outro are chosen independently. They may be different files, or the
same file in both fields. When both fields reference the same asset, the Worker
reuses the intro's loudness analysis for the outro instead of analyzing the file
twice.

## 11. Compile

With the collection configured and clips present, compile them:

- Use the **Recompile** action on a clip row in the Library Manager's clip
  table to compile one clip.
- Use the `cinema_collections.compile_all` or `cinema_collections.compile_collection`
  service, or the corresponding **Compile all collections** button, to compile
  every enabled collection or one collection.

The Library Manager polls the queued job and shows its live stage and
percentage while it runs. The integration's **Current job progress** sensor
(`current_job_progress`) reports the same percentage for your dashboard.

## 12. Select the result from an automation

Verify selection before wiring it into an automation. In Developer Tools, call
the service with a dry run:

```yaml
service: cinema_collections.select_next_clip
data:
  dry_run: true
response_variable: next_clip
```

The response contains `collection_id`, `clip_id`, `relative_output_path`,
`media_content_id`, `media_content_type`, `duration_seconds`, and
`history_reset`. Check the returned `media_content_id` against your media
player, then remove `dry_run` so real selections record playback history. See
[dashboard.md](dashboard.md) for the response shape and a full automation
pattern.

Cinema Collections selects media; it never plays it and never touches a device.
Your existing playback automation remains the device-control layer.

## What to read next

- [installation.md](installation.md) — install options for supervised and
  external Docker deployments.
- [configuration.md](configuration.md) — Worker options, pairing, global
  options, and the collection and profile subentry forms.
- [processing-profiles.md](processing-profiles.md) — the complete profile field
  reference.
- [dashboard.md](dashboard.md) — sensors, buttons, and the automation pattern.
- [api.md](api.md) — the Worker API the integration uses.
- [troubleshooting.md](troubleshooting.md) — common failures and their fixes.
- [security.md](security.md) — credential handling and filesystem boundaries.