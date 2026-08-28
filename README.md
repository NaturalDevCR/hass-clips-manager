# Cinema Collections

Cinema Collections is a Home Assistant App and integration for organizing and
discovering cinema content in a local media library.

## Installation

### Home Assistant

1. Add this repository to HACS as a custom repository, selecting the
   **Integration** category.
2. Install **Cinema Collections** from HACS.
3. Restart Home Assistant and add the integration from **Settings → Devices &
   services → Add integration**.

### App

Install the Cinema Collections App from the repository's Home Assistant App
repository. Configure the app's media-library settings, then use the
integration to connect it to Home Assistant.

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
