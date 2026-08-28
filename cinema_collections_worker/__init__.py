"""Compatibility package for the Cinema Collections worker.

The application source lives in ``app/src``; this small package keeps direct
checkout imports working for tooling that imports the repository root.
"""

from pathlib import Path

_src_package = Path(__file__).resolve().parent.parent / "app" / "src" / "cinema_collections_worker"
if _src_package.is_dir():
    __path__.append(str(_src_package))
