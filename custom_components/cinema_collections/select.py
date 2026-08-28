"""Collection override Select entity."""
# pyright: reportIncompatibleVariableOverride=false

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import CinemaCollectionsCoordinator, override_for_entry, policies_for_entry
from .resolver import OverrideKind
from .services import async_set_collection_override


@dataclass(frozen=True, kw_only=True)
class CinemaCollectionsSelectDescription(SelectEntityDescription):
    """Description for the sole collection policy control."""


SELECT_DESCRIPTION = CinemaCollectionsSelectDescription(
    key="collection_override", translation_key="collection_override"
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the entry's override selector."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CinemaCollectionsSelect(hass, entry, runtime.coordinator)])


class CinemaCollectionsSelect(SelectEntity):
    """Set a durable automatic/default/explicit OverrideMode."""

    entity_description = SELECT_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: CinemaCollectionsCoordinator
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_{SELECT_DESCRIPTION.key}"

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def current_option(self) -> str:
        """Use simple stable IDs for selector options and automation input."""
        override = override_for_entry(self._entry)
        if override.kind is OverrideKind.EXPLICIT:
            assert override.collection_id is not None
            return override.collection_id
        return override.kind.value

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def options(self) -> list[str]:
        """Expose only manual-override-eligible configured collections."""
        return [
            OverrideKind.AUTOMATIC.value,
            OverrideKind.DEFAULT.value,
            *(
                policy.id
                for policy in policies_for_entry(self._entry)
                if policy.enabled and policy.allow_manual_override
            ),
        ]

    async def async_select_option(self, option: str) -> None:
        """Persist the selected OverrideMode and request a policy refresh."""
        await async_set_collection_override(
            self.hass,
            self._entry,
            {"option": option},
            coordinator=self._coordinator,
        )
