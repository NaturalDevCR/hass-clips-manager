"""Config flow for pairing a Cinema Collections Worker."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import (
    WorkerApiAuthenticationError,
    WorkerApiClient,
    WorkerApiCompatibilityError,
    WorkerApiConnectionError,
    WorkerApiError,
    normalize_endpoint,
)
from .const import (
    CLIENT_VERSION,
    CONF_ENDPOINT,
    CONF_MEDIA_URI_PREFIX,
    CONF_TOKEN,
    DEFAULT_MEDIA_URI_PREFIX,
    DOMAIN,
    EXPECTED_WORKER_COMPONENT,
)
from .models import WorkerHealth
from .options_flow import CinemaCollectionsOptionsFlow
from .selection import normalize_media_uri_prefix
from .subentries import CollectionSubentryFlow, ProfileSubentryFlow


class CinemaCollectionsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Pair Home Assistant with an authenticated, compatible Worker."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Expose global collection policy in the native Options UI."""
        return CinemaCollectionsOptionsFlow()

    @classmethod
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Expose Worker-backed profile and collection configuration subentries."""
        return {
            "collection": CollectionSubentryFlow,
            "profile": ProfileSubentryFlow,
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Validate the Worker endpoint, credential, and API compatibility."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                endpoint = normalize_endpoint(user_input[CONF_ENDPOINT])
                raw_token = user_input[CONF_TOKEN]
                if not isinstance(raw_token, str) or not raw_token.strip():
                    raise ValueError("Worker credential cannot be empty")
                token = raw_token.strip()
                media_uri_prefix = normalize_media_uri_prefix(
                    user_input.get(CONF_MEDIA_URI_PREFIX, DEFAULT_MEDIA_URI_PREFIX)
                )
                await self.async_set_unique_id(endpoint)
                self._abort_if_unique_id_configured()
                if any(
                    entry.data.get(CONF_ENDPOINT) == endpoint
                    for entry in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                client = WorkerApiClient(endpoint, token, async_get_clientsession(self.hass))
                health = await client.async_health()
                _validate_compatibility(health)
            except WorkerApiAuthenticationError:
                errors["base"] = "invalid_auth"
            except WorkerApiCompatibilityError:
                errors["base"] = "incompatible_worker"
            except (WorkerApiConnectionError, WorkerApiError):
                errors["base"] = "cannot_connect"
            except (KeyError, ValueError):
                errors["base"] = "invalid_endpoint"
            else:
                return self.async_create_entry(
                    title="Cinema Collections Worker",
                    data={
                        CONF_ENDPOINT: endpoint,
                        CONF_TOKEN: token,
                        CONF_MEDIA_URI_PREFIX: media_uri_prefix,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENDPOINT): str,
                    vol.Required(CONF_TOKEN): str,
                    vol.Optional(CONF_MEDIA_URI_PREFIX, default=DEFAULT_MEDIA_URI_PREFIX): str,
                }
            ),
            errors=errors,
        )


def _validate_compatibility(health: WorkerHealth) -> None:
    """Reject Worker API and declared client ranges unsupported by this integration."""
    if health.component != EXPECTED_WORKER_COMPONENT:
        raise WorkerApiCompatibilityError("Worker component identity is incompatible")
    client_version = _parse_version(CLIENT_VERSION)
    if _parse_version(health.api_version)[0] != client_version[0]:
        raise WorkerApiCompatibilityError("Worker API major version is incompatible")
    if client_version < _parse_version(health.min_client_version):
        raise WorkerApiCompatibilityError("Worker requires a newer integration")
    if not _matches_max_version(client_version, health.max_client_version):
        raise WorkerApiCompatibilityError("Worker no longer supports this integration")


def _parse_version(value: str) -> tuple[int, ...]:
    """Parse a numeric semantic version into a comparable tuple."""
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise WorkerApiCompatibilityError("Worker returned an invalid compatibility version")
    return tuple(int(part) for part in parts)


def _matches_max_version(client_version: tuple[int, ...], maximum: str) -> bool:
    """Compare a client version to exact or trailing-`x` Worker maxima."""
    parts = maximum.split(".")
    if not parts:
        raise WorkerApiCompatibilityError("Worker returned an invalid compatibility version")
    numeric: list[int] = []
    for index, part in enumerate(parts):
        if part == "x" and index == len(parts) - 1:
            return client_version[:index] == tuple(numeric)
        if not part.isdigit():
            raise WorkerApiCompatibilityError("Worker returned an invalid compatibility version")
        numeric.append(int(part))
    return client_version <= tuple(numeric)
