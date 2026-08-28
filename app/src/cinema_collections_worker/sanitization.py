"""Bounded redaction for operational messages exposed outside the process."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_message(
    value: object,
    *,
    roots: Iterable[Path] = (),
    secrets: Iterable[str] = (),
    limit: int = 1000,
) -> str:
    """Redact credentials, canonical roots, controls, and excess output."""

    rendered = _CONTROL.sub("?", str(value))
    rendered = _BEARER.sub("Bearer [REDACTED]", rendered)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "[REDACTED]")
    for root in sorted(
        {str(Path(path).resolve(strict=False)) for path in roots}, key=len, reverse=True
    ):
        rendered = rendered.replace(root, "[WORKER_ROOT]")
    return rendered[-limit:]
