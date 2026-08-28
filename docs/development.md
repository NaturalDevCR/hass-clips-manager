# Development

Cinema Collections targets Python 3.13. Create a virtual environment and
install the development and package dependencies with your preferred Python
package manager. With `uv`, for example:

```console
uv sync --all-groups
```

Run the release gate used by continuous integration from the repository root:

```console
scripts/verify.sh
```

The gate validates formatting, linting, types, the full test suite, OpenAPI,
repository metadata, JSON translations, and the App Docker image. Docker is
required for the image-build step in CI; a local run reports that it skipped
only that step when Docker is unavailable.

The Worker roundtrip tests boot FastAPI in process with a generated bearer
token and temporary allowlisted media roots. Most compilation cases use an
FFmpeg stand-in; the fixture pipeline generates a one-second color-and-tone
clip with local FFmpeg when it is available. Neither path starts Home Assistant
or interacts with playback or any real device.

The worker package lives at `cinema_collections_worker`; the Home Assistant
integration lives at `custom_components/cinema_collections`.
