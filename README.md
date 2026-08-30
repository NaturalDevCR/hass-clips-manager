# Cinema Collections

Cinema Collections is a Home Assistant App and integration for organizing and
discovering cinema content in a local media library.

It has two components:

- **Cinema Collections Worker** — a Supervisor App that catalogs source clips,
  compiles them into intro/outro-ready clips with an FFmpeg queue, and exposes a
  Library Manager through App Ingress. The Library Manager imports and scans
  clips, uploads intro/outro assets, recompiles or recovers catalog items, and
  shows live job progress.
- **Cinema Collections integration** — a HACS integration that pairs with the
  Worker, stores collection policy and typed processing profiles (edited through
  a field-by-field form, not JSON), and exposes monitoring sensors, buttons, a
  collection-override Select, and the `select_next_clip` automation service.

The integration only selects media; it never plays it and never touches a
device.

## Getting started

Follow [the getting-started walkthrough](docs/getting-started.md) to go from a
fresh install to a compiled clip selected by an automation.

## Installation

### Home Assistant

1. Add this repository to HACS as a custom repository, selecting the
   **Integration** category.
2. Install **Cinema Collections** from HACS, picking an `integration-vX.Y.Z`
   release tag.
3. Restart Home Assistant and add the integration from **Settings → Devices &
   services → Add integration**.

### App

Install the **Cinema Collections Worker** App from the repository's Home
Assistant App repository. Configure the App's media-library settings and bearer
secret, then use the integration to connect it to Home Assistant.

See [the development guide](docs/development.md) for local setup and tests.

## Release verification

Run `scripts/verify.sh` before releasing or opening a pull request. It checks
formatting, linting, types, the full contract and integration test suite, the
published OpenAPI document, repository metadata, translations, and the App
image. The integration/Worker roundtrip test uses generated tokens and
temporary media roots only; it never starts a Home Assistant installation or
controls playback or other devices. If Docker is not installed locally, the
image-build portion is skipped locally and remains required in CI.

## License

Cinema Collections is licensed under the Apache License, Version 2.0.