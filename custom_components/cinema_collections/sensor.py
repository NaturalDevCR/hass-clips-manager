"""Monitoring sensors for Cinema Collections without device control."""
# pyright: reportIncompatibleVariableOverride=false

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CinemaCollectionsCoordinator, CoordinatorSnapshot


@dataclass(frozen=True, kw_only=True)
class CinemaCollectionsSensorDescription(SensorEntityDescription):
    """Description for one stable, translated integration sensor."""


SENSOR_DESCRIPTIONS: tuple[CinemaCollectionsSensorDescription, ...] = (
    CinemaCollectionsSensorDescription(
        key="active_collection", translation_key="active_collection"
    ),
    CinemaCollectionsSensorDescription(
        key="processing_status", translation_key="processing_status"
    ),
    CinemaCollectionsSensorDescription(
        key="queue_depth",
        translation_key="queue_depth",
        native_unit_of_measurement="jobs",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CinemaCollectionsSensorDescription(key="latest_error", translation_key="latest_error"),
    CinemaCollectionsSensorDescription(
        key="current_job_progress",
        translation_key="current_job_progress",
        native_unit_of_measurement="%",
    ),
    CinemaCollectionsSensorDescription(key="worker_version", translation_key="worker_version"),
    CinemaCollectionsSensorDescription(key="next_schedule", translation_key="next_schedule"),
    CinemaCollectionsSensorDescription(
        key="collection_priorities", translation_key="collection_priorities"
    ),
    CinemaCollectionsSensorDescription(
        key="compilation_summary", translation_key="compilation_summary"
    ),
    CinemaCollectionsSensorDescription(key="clip_states", translation_key="clip_states"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add policy and Worker status sensors for one integration entry."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: CinemaCollectionsCoordinator = runtime.coordinator
    async_add_entities(
        CinemaCollectionsSensor(coordinator, description, entry.entry_id)
        for description in SENSOR_DESCRIPTIONS
    )


class CinemaCollectionsSensor(CoordinatorEntity[CinemaCollectionsCoordinator], SensorEntity):
    """Represent one coordinator field as a native Home Assistant sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CinemaCollectionsCoordinator,
        description: CinemaCollectionsSensorDescription,
        entry_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def available(self) -> bool:
        """Keep the local active policy visible while Worker metrics degrade."""
        if self.entity_description.key == "active_collection":
            return True
        return self.coordinator.data.available

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def native_value(self) -> str | int | float | None:
        """Return a simple state while details remain in attributes."""
        snapshot = self.coordinator.data
        match self.entity_description.key:
            case "active_collection":
                return snapshot.active_collection_id
            case "processing_status":
                return _processing_state(snapshot)
            case "queue_depth":
                return snapshot.queue_depth
            case "latest_error":
                return snapshot.latest_error
            case "current_job_progress":
                progress = snapshot.progress
                percent = progress.get("percent") if progress is not None else None
                return percent if isinstance(percent, (int, float)) else None
            case "worker_version":
                return snapshot.health.worker_version if snapshot.health is not None else None
            case "next_schedule":
                return snapshot.next_schedule.isoformat() if snapshot.next_schedule else None
            case "collection_priorities":
                return len(snapshot.priorities)
            case "compilation_summary":
                return snapshot.compilation_summary
            case "clip_states":
                return sum(snapshot.clip_states.values())
            case _:
                return None

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def extra_state_attributes(self) -> dict[str, Any]:
        """Publish reason, queue, progress, and compatibility for dashboards."""
        snapshot = self.coordinator.data
        attributes: dict[str, Any] = {
            "reason": snapshot.reason,
            "override_rejected": snapshot.override_rejected,
            "queue_depth": snapshot.queue_depth,
            "progress": dict(snapshot.progress) if snapshot.progress is not None else None,
            "compatibility": (
                dict(snapshot.compatibility) if snapshot.compatibility is not None else None
            ),
            "worker_error": snapshot.error,
            "next_schedule": (
                snapshot.next_schedule.isoformat() if snapshot.next_schedule else None
            ),
            "collection_priorities": dict(snapshot.priorities),
            "compilation_summary": snapshot.compilation_summary,
            "clip_states": dict(snapshot.clip_states),
            "history": dict(snapshot.history),
        }
        if snapshot.status is not None and snapshot.status.current_job is not None:
            attributes["current_job"] = dict(snapshot.status.current_job)
        return attributes


def _processing_state(snapshot: CoordinatorSnapshot) -> str:
    """Return the Worker job state without treating a disconnected Worker as idle."""
    if not snapshot.available:
        return "unavailable"
    if snapshot.status is None or snapshot.status.current_job is None:
        return "idle"
    state = snapshot.status.current_job.get("state")
    return state if isinstance(state, str) else "active"
