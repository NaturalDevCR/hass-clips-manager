"""Explicit operational buttons for Cinema Collections."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .services import async_run_action


@dataclass(frozen=True, kw_only=True)
class CinemaCollectionsButtonDescription(ButtonEntityDescription):
    """Description for an intentional Worker or history request."""


BUTTON_DESCRIPTIONS: tuple[CinemaCollectionsButtonDescription, ...] = (
    CinemaCollectionsButtonDescription(key="scan_library", translation_key="scan_library"),
    CinemaCollectionsButtonDescription(key="compile_all", translation_key="compile_all"),
    CinemaCollectionsButtonDescription(key="retry_failed", translation_key="retry_failed"),
    CinemaCollectionsButtonDescription(
        key="cancel_processing", translation_key="cancel_processing"
    ),
    CinemaCollectionsButtonDescription(
        key="cancel_all_processing", translation_key="cancel_all_processing"
    ),
    CinemaCollectionsButtonDescription(
        key="cleanup_temporaries", translation_key="cleanup_temporaries"
    ),
    CinemaCollectionsButtonDescription(key="reset_history", translation_key="reset_history"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add explicitly-invoked operational controls; no device controls exist."""
    async_add_entities(
        CinemaCollectionsButton(hass, entry, description) for description in BUTTON_DESCRIPTIONS
    )


class CinemaCollectionsButton(ButtonEntity):
    """Dispatch a validated service action without manipulating any device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: CinemaCollectionsButtonDescription,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    async def async_press(self) -> None:
        """Route the requested operation through the same validated service layer."""
        await async_run_action(self.hass, self._entry, self.entity_description.key, {})
