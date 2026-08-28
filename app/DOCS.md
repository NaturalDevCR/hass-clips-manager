# Cinema Collections Worker

This App keeps the media catalog, processing queue, logs, and recoverable Library Manager trash in its `/data` mount. It reads source clips from `source_root` and writes compiled clips to `compiled_root`; both must remain below the App's `/media` mount.

Set a long, unique `bearer_secret` before starting the App. The Worker does not publish a host port: Home Assistant reaches the manager through App Ingress, while the integration uses the private container endpoint.

The Library Manager can import source clips, rename them within their selected collection, record tags and notes, request scans/recompiles, recover selected source/output files from Worker-owned trash, and permanently delete one explicitly selected catalog item after confirmation. Trash is not automatically purged. Deleting source does not delete compiled output, and vice versa.

No custom Lovelace card is installed. Use the App Ingress panel for file management and the integration's native Home Assistant entities and services for monitoring and controls.
