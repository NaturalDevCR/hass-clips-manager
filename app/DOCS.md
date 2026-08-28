# Cinema Collections Worker

This App keeps the media catalog, processing queue, bounded logs, and recoverable Library Manager trash in its `/data` mount. It reads source clips from `source_root` and writes compiled clips to `compiled_root`; both must remain below the App's `/media` mount and must be different directories.

Before starting, replace `bearer_secret` with a long random value. Keep it private: never place it in a URL, dashboard, log, or automation. The Worker does not publish a host port. Home Assistant reaches the manager through App Ingress, while the integration uses the private App endpoint and the same bearer token.

Create collections and typed processing profiles through the integration. Start with the Compatibility 4K Loudness Profile unless you intentionally need another profile. Keep a disk reserve configured and back up `/data` before upgrades; it contains the Worker SQLite catalog, audit state, and recoverable trash.

The Library Manager can import source clips, rename them within their selected collection, record tags and notes, request scans/recompiles, recover selected source/output files from Worker-owned trash, and permanently delete one explicitly selected catalog item after confirmation. Trash is not automatically purged. Deleting source does not delete compiled output, and vice versa.

No custom Lovelace card is installed. Use App Ingress for file management and the integration's native Home Assistant entities and services for monitoring and selection. The Worker and integration never control playback, Cast, projectors, screens, music, or any other device. See the repository's `docs/` directory for external Docker, dashboard, migration, and security guidance.
