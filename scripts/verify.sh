#!/usr/bin/env bash
# Run the release gate from the repository root.  The test suite uses only
# temporary media roots and generated fixtures; it never contacts Home Assistant.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repository_root"

uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run openapi-spec-validator contract/openapi-v1.yaml
uv run pytest tests/test_repository_metadata.py -q

for translation in custom_components/cinema_collections/translations/*.json; do
    uv run python -m json.tool "$translation" >/dev/null
done

if command -v docker >/dev/null 2>&1; then
    docker build --file app/Dockerfile --tag cinema-collections-worker:verify app
else
    echo "Docker is unavailable; skipped the local image build (CI runs it)." >&2
fi
