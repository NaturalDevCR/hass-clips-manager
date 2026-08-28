"""No-YAML global configuration flow for collection policy."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector

from .const import CONF_OVERRIDE_COLLECTION_ID, CONF_OVERRIDE_MODE
from .resolver import OverrideKind, OverrideMode

OVERRIDE_MODE_SELECTOR: Any = selector.SelectSelector(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    {
        "options": [item.value for item in OverrideKind],
        "translation_key": "override_mode",
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
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_override"
            else:
                result = {
                    **defaults,
                    CONF_OVERRIDE_MODE: mode.value,
                    CONF_OVERRIDE_COLLECTION_ID: selected
                    if mode is OverrideKind.EXPLICIT
                    else None,
                }
                return self.async_create_entry(title="", data=result)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_OVERRIDE_MODE,
                        default=defaults.get(CONF_OVERRIDE_MODE, OverrideKind.AUTOMATIC.value),
                    ): OVERRIDE_MODE_SELECTOR,
                    vol.Optional(
                        CONF_OVERRIDE_COLLECTION_ID,
                        default=defaults.get(CONF_OVERRIDE_COLLECTION_ID, ""),
                    ): str,
                }
            ),
            errors=errors,
        )
