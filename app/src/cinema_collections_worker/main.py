"""Uvicorn entry point for the local Cinema Collections Worker."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from .api import create_app
from .settings import Settings


def main() -> None:
    """Start the Worker using an explicitly supplied options file."""

    options_path = Path(os.environ.get("CINEMA_COLLECTIONS_OPTIONS", "/data/options.yaml"))
    settings = Settings.load(options_path)
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
