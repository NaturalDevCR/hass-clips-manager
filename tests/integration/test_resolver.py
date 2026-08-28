"""Policy resolution coverage for collection selection."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.cinema_collections.resolver import (
    CollectionPolicy,
    OverrideMode,
    SelectionReason,
    resolve_active_collection,
)


def _policy(identifier: str, **changes: object) -> CollectionPolicy:
    return CollectionPolicy(id=identifier, **changes)


def test_resolver_uses_half_open_bounded_and_unbounded_windows() -> None:
    now = datetime(2026, 8, 27, 12, tzinfo=UTC)
    bounded = _policy(
        "bounded",
        starts_at=datetime(2026, 8, 27, 12, tzinfo=UTC),
        ends_at=datetime(2026, 8, 27, 13, tzinfo=UTC),
    )
    unbounded = _policy("unbounded", priority=-1)

    assert (
        resolve_active_collection([bounded, unbounded], OverrideMode.automatic(), now).id
        == "bounded"
    )
    assert (
        resolve_active_collection(
            [bounded, unbounded], OverrideMode.automatic(), datetime(2026, 8, 27, 13, tzinfo=UTC)
        ).id
        == "unbounded"
    )


def test_resolver_ignores_disabled_scheduled_collections() -> None:
    result = resolve_active_collection(
        [_policy("disabled", enabled=False, priority=100), _policy("active", priority=1)],
        OverrideMode.automatic(),
        datetime(2026, 8, 27, 12, tzinfo=UTC),
    )

    assert result.id == "active"
    assert result.reason is SelectionReason.SCHEDULE


def test_resolver_rejects_explicit_override_when_not_permitted() -> None:
    result = resolve_active_collection(
        [_policy("private", allow_manual_override=False), _policy("fallback", is_default=True)],
        OverrideMode.explicit("private"),
        datetime(2026, 8, 27, 12, tzinfo=UTC),
    )

    assert result.id == "fallback"
    assert result.reason is SelectionReason.DEFAULT
    assert result.override_rejected is True


def test_resolver_uses_enabled_default_when_requested() -> None:
    result = resolve_active_collection(
        [_policy("default", is_default=True), _policy("scheduled", priority=999)],
        OverrideMode.default(),
        datetime(2026, 8, 27, 12, tzinfo=UTC),
    )

    assert result.id == "default"
    assert result.reason is SelectionReason.MANUAL_DEFAULT


def test_resolver_uses_priority_then_stable_identifier() -> None:
    result = resolve_active_collection(
        [_policy("zeta", priority=5), _policy("alpha", priority=5)],
        OverrideMode.automatic(),
        datetime(2026, 8, 27, 12, tzinfo=UTC),
    )

    assert result.id == "alpha"
    assert result.reason is SelectionReason.SCHEDULE


def test_resolver_reports_no_collection_without_an_enabled_default() -> None:
    result = resolve_active_collection(
        [_policy("expired", ends_at=datetime(2026, 8, 27, 11, tzinfo=UTC))],
        OverrideMode.automatic(),
        datetime(2026, 8, 27, 12, tzinfo=UTC),
    )

    assert result.collection is None
    assert result.reason is SelectionReason.NONE
