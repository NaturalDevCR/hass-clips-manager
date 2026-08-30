# Installation

This page covers the two install mechanics. For a complete end-to-end setup —
pairing, collections, profiles, compiling, and automation — follow
[getting-started.md](getting-started.md).

Cinema Collections has two supported Worker deployments: the supervised Home Assistant App and external Docker. Install the HACS integration separately in either case. Do not expose a Worker to the public internet.

## Home Assistant App (recommended for supervised installs)

1. Add this repository to the Home Assistant App store and install **Cinema Collections Worker**. Add the plain repository root URL
   (`https://github.com/NaturalDevCR/hass-clips-manager`) — the Supervisor cannot clone a URL that contains `/tree/main/...`.
2. Before starting it, replace the generated `bearer_secret` with a long random value. Treat it as a password and do not paste it into logs, dashboards, or automation YAML.
3. Set `source_root` and `compiled_root` below the App's `/media` mount. Keep them separate; the Worker reads source clips from the former and creates compiled clips in the latter.
4. Start the App. It uses App Ingress for its Library Manager and does not publish a host port.
5. In Home Assistant, add the Cinema Collections integration and enter the Worker’s private endpoint plus the same bearer token. The config flow verifies authentication and API compatibility.

This repository is a monorepo with two separately installable components, so it
releases two tag series. HACS lists every release tag: pick an
`integration-vX.Y.Z` tag for the integration. Tags named `worker-vX.Y.Z` belong
to the App, which updates through the Supervisor, not through HACS.

The App maps only its data, App configuration, and media areas. It does not need, and must not be granted, the full Home Assistant configuration directory.

## External Docker

Run the image on a private Docker network shared with Home Assistant (or a private, routed network). Do not publish a port by default. Supply an options file with `mode: external`, a random `bearer_secret`, an explicit `media_root`, and source/compiled roots that are descendants of that media root. Mount only the Worker data directory and the selected media root; use separate read/write permissions where your platform can enforce them.

Example Compose shape (replace placeholders, not literal values):

```yaml
services:
  cinema-collections-worker:
    image: ghcr.io/naturaldevcr/cinema-collections-worker:stable
    networks: [private]
    volumes:
      - worker-data:/data
      - /srv/cinema-media:/media
    environment:
      CINEMA_COLLECTIONS_OPTIONS: /data/options.yaml
networks:
  private:
    internal: true
volumes:
  worker-data:
```

If LAN access is genuinely required, bind only to a private interface, restrict ingress to trusted private CIDRs, use TLS at a trusted reverse proxy, and keep bearer authentication enabled. The integration endpoint is the private HTTPS or HTTP base URL; never include a token in the URL.
