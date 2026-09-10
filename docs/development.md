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
repository metadata, JSON translations, the Library Manager interface, and the
App Docker image. Node and Docker are required for the interface and image
steps in CI; a local run reports which of those it skipped when the tool is
unavailable.

The Worker roundtrip tests boot FastAPI in process with a generated bearer
token and temporary allowlisted media roots. Most compilation cases use an
FFmpeg stand-in; the fixture pipeline generates a one-second color-and-tone
clip with local FFmpeg when it is available. Neither path starts Home Assistant
or interacts with playback or any real device.

The worker package lives at `cinema_collections_worker`; the Home Assistant
integration lives at `custom_components/cinema_collections`.

## The Library Manager interface

The Library Manager is a Vue 3 single-page application under `app/ui/`, built with
Vite and Tailwind. Install its dependencies and run its own checks with:

```console
npm --prefix app/ui install
npm --prefix app/ui run test:unit
npm --prefix app/ui run build
```

`npm --prefix app/ui run dev` serves the interface with hot reload and proxies
`/manager` to a Worker on port 8099, so run the Worker alongside it.

The build writes `app/ui/dist`, which is never committed: the App image builds it
in a Node stage and copies it to the Worker's `static/ui` directory. The interface
lives inside `app/` because the Supervisor builds a local add-on with the add-on
folder as the Docker build context, so nothing the image needs may sit outside it. A Worker
started without that bundle answers `GET /` with 503 naming the build command,
so copy `app/ui/dist` there when running the Worker directly from a checkout.

Two constraints hold for anything the interface fetches. Home Assistant serves
the App under a per-install Ingress prefix, so every Worker URL must be
relative and the router runs in hash mode; the sole exception is the Home
Assistant order bridge, which is built as a same-origin absolute URL because it
lives at the domain root, outside that prefix.
