"""Shared fixtures for the Worker's HTTP tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cinema_collections_worker import manager_web

# The Library Manager shell is produced by the UI build (ui/dist) and copied
# into the package by the App image. A source checkout has no bundle, so these
# tests stage a stand-in shell: they cover the Worker's HTTP contract, while
# the interface itself is covered by the Vitest suite in ui/.
_STUB_SHELL = (
    "<!doctype html>\n"
    '<html lang="en"><head><meta charset="utf-8">\n'
    "<title>Cinema Collections Library Manager</title></head>\n"
    '<body><div id="app"></div></body></html>\n'
)


@pytest.fixture(autouse=True)
def manager_ui_shell() -> Iterator[Path]:
    root = Path(manager_web.__file__).parent / "static" / "ui"
    shell = root / "index.html"
    # A test may delete the shell to exercise the unbuilt-interface path, so a
    # real local build is restored afterwards rather than left destroyed.
    original = shell.read_bytes() if shell.is_file() else None
    if original is None:
        root.mkdir(parents=True, exist_ok=True)
        shell.write_text(_STUB_SHELL, encoding="utf-8")
    try:
        yield shell
    finally:
        if original is None:
            shell.unlink(missing_ok=True)
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        elif not shell.is_file():
            shell.write_bytes(original)
