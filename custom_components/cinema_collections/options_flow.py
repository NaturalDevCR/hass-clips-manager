"""No-YAML global configuration flow for collection policy."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_HISTORY_RESET_MODE,
    CONF_HISTORY_RESET_TIME,
    CONF_OVERRIDE_COLLECTION_ID,
    CONF_OVERRIDE_MODE,
    CONF_SYNC_ON_STARTUP,
    DEFAULT_HISTORY_RESET_MODE,
    DEFAULT_HISTORY_RESET_TIME,
    DEFAULT_SYNC_ON_STARTUP,
    HistoryResetMode,
)
from .resolver import OverrideKind, OverrideMode

OVERRIDE_MODE_SELECTOR: Any = selector.SelectSelector(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    {
        "options": [item.value for item in OverrideKind],
        "translation_key": "override_mode",
    }
)

HISTORY_RESET_MODE_SELECTOR: Any = selector.SelectSelector(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    {
        "options": [item.value for item in HistoryResetMode],
        "translation_key": "history_reset_mode",
    }
)


def _options_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    """Build the global options form with the entry's current values as defaults."""
    return vol.Schema(
        {
            vol.Required(
                CONF_OVERRIDE_MODE,
                default=defaults.get(CONF_OVERRIDE_MODE, OverrideKind.AUTOMATIC.value),
            ): OVERRIDE_MODE_SELECTOR,
            vol.Optional(
                CONF_OVERRIDE_COLLECTION_ID,
                default=defaults.get(CONF_OVERRIDE_COLLECTION_ID, ""),
            ): str,
            vol.Required(
                CONF_HISTORY_RESET_MODE,
                default=defaults.get(CONF_HISTORY_RESET_MODE, DEFAULT_HISTORY_RESET_MODE),
            ): HISTORY_RESET_MODE_SELECTOR,
            vol.Required(
                CONF_HISTORY_RESET_TIME,
                default=defaults.get(CONF_HISTORY_RESET_TIME, DEFAULT_HISTORY_RESET_TIME),
            ): str,
            vol.Required(
                CONF_SYNC_ON_STARTUP,
                default=defaults.get(CONF_SYNC_ON_STARTUP, DEFAULT_SYNC_ON_STARTUP),
            ): bool,
        }
    )


class CinemaCollectionsOptionsFlow(config_entries.OptionsFlow):
    """Manage global, durable collection-selection preferences."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Select automatic/default/manual behavior without editing YAML."""
        errors: dict[str, str] = {}
        defaults = dict(self.config_entry.options)
        if user_input is not None:
            try:
                mode = OverrideKind(user_input[CONF_OVERRIDE_MODE])
                selected = user_input.get(CONF_OVERRIDE_COLLECTION_ID) or None
                if mode is OverrideKind.EXPLICIT:
                    OverrideMode.explicit(str(selected))
                elif selected is not None:
                    raise ValueError("only explicit mode may include a collection ID")
                reset_mode = HistoryResetMode(
                    user_input.get(CONF_HISTORY_RESET_MODE, DEFAULT_HISTORY_RESET_MODE)
                )
                reset_time = time.fromisoformat(str(user_input[CONF_HISTORY_RESET_TIME]))
                if reset_time.second or reset_time.microsecond:
                    raise ValueError("history reset time must use minute precision")
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_override"
            else:
                result = {
                    **defaults,
                    CONF_OVERRIDE_MODE: mode.value,
                    CONF_OVERRIDE_COLLECTION_ID: selected
                    if mode is OverrideKind.EXPLICIT
                    else None,
                    CONF_HISTORY_RESET_MODE: reset_mode.value,
                    CONF_HISTORY_RESET_TIME: reset_time.strftime("%H:%M"),
                    CONF_SYNC_ON_STARTUP: bool(user_input[CONF_SYNC_ON_STARTUP]),
                }
                return self.async_create_entry(title="", data=result)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(defaults),
            errors=errors,
        )
