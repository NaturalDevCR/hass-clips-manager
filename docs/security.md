# Security

Cinema Collections is designed to manage media metadata and processing, not devices. It never controls media players, Cast, projectors, screens, music, or any physical hardware. Device ownership remains with the user’s existing Home Assistant automations.

## Credentials and networking

The Worker requires a high-entropy bearer secret. Store it only in the Worker options and integration config entry, rotate it after suspected exposure, and never place it in a URL. Requests use an Authorization header; diagnostics, logs, and errors must not contain that header or its value.

The supervised App uses private App networking and Ingress rather than a public host port. External Docker should use a private network without published ports. If a LAN endpoint is unavoidable, allow only trusted private CIDRs and terminate TLS at a trusted reverse proxy. Do not expose the Worker directly to the internet.

## Filesystem safety

Mount only the Worker data and required media directories. Never mount the entire Home Assistant configuration directory. The Worker canonicalizes every path and only accepts root-relative paths under configured source, compiled, temporary, and assets roots. It rejects traversal, absolute user input, escaping symlinks, arbitrary shell commands, and raw FFmpeg filters.

Temporary cleanup is limited to Worker-tracked files below its temporary root. Outputs publish atomically after validation. Source and compiled media are never automatically deleted. Permanent deletion requires a selected catalog item, a validated allowlisted path, a second confirmation, and an audit event.

## Support data

Before sharing diagnostics, still review them for your environment. The integration recursively omits secret fields, strips authorization text, replaces configured-root paths with relative labels, redacts any other absolute path, and limits error excerpts. It retains compatibility versions and bounded queue/progress state so a support report remains useful without exposing media layout or credentials.
