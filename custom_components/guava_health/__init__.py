"""Unofficial Home Assistant integration for logging doses to Guava Health."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import time
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .api import GuavaAuthError, GuavaClient, GuavaError, resolve_medication
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_COOLDOWN,
    CONF_MEDICATIONS,
    CONF_TOKEN,
    DEFAULT_COOLDOWN,
    DOMAIN,
    EVENT_DOSE_LOGGED,
    SERVICE_LOG_MEDICATION,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("medication"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("quantity"): vol.All(
            vol.Coerce(float), vol.Range(min=0.001, max=1000)
        ),
        vol.Optional("notes"): vol.All(cv.string, vol.Length(max=10000)),
        vol.Optional("time"): cv.datetime,
        vol.Optional("bypass_cooldown", default=False): cv.boolean,
        vol.Optional("allow_new", default=False): cv.boolean,
    }
)


def token_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Rotated tokens live here: config entries save with a delay, this saves at once."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.token", private=True)


@dataclass
class GuavaProfile:
    """Runtime state of one person's entry."""

    hass: HomeAssistant
    entry: ConfigEntry
    client: GuavaClient
    known_medications: list[str] = field(default_factory=list)
    last_logged: dict[str, float] = field(default_factory=dict)

    async def log_dose(
        self,
        medication: str,
        *,
        quantity: float | None = None,
        notes: str | None = None,
        when: datetime | None = None,
        bypass_cooldown: bool = False,
    ) -> None:
        """Log a dose under its exact stored name, guarding against double presses."""
        key = medication.strip().casefold()
        cooldown = self.entry.options.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
        previous = self.last_logged.get(key)
        now = time.monotonic()
        if (
            not bypass_cooldown
            and previous is not None
            and now - previous < cooldown
        ):
            raise HomeAssistantError(
                f"{medication.strip()} was logged {int(now - previous)} seconds ago; "
                f"ignored by the {cooldown} second cooldown"
            )
        # Set before the first await so a second press in flight is caught too.
        self.last_logged[key] = now

        moment = when or dt_util.now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt_util.get_default_time_zone())
        try:
            result = await self.client.log_dose(
                medication,
                moment.isoformat(timespec="seconds"),
                f"ha-{uuid.uuid4()}",
                quantity,
                notes,
            )
        except GuavaAuthError as err:
            self._forget(key, previous)
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                f"Guava needs re-authorising for {self.entry.title}"
            ) from err
        except GuavaError as err:
            # The outcome of a failed write can be unknown, but a press that shows an
            # error should be retryable; the user checks the app before retrying.
            self._forget(key, previous)
            raise HomeAssistantError(f"Dose not confirmed by Guava: {err}") from err

        self.hass.bus.async_fire(
            EVENT_DOSE_LOGGED,
            {
                "config_entry_id": self.entry.entry_id,
                "profile": self.entry.title,
                "medication": medication.strip(),
                "quantity": quantity if quantity is not None else 1,
                "time": moment.isoformat(timespec="seconds"),
                "environment": result.get("environment"),
            },
        )

    def _forget(self, key: str, previous: float | None) -> None:
        if previous is None:
            self.last_logged.pop(key, None)
        else:
            self.last_logged[key] = previous

    async def refresh_known(self) -> None:
        """Reload the active medication list; keep the old one on failure."""
        try:
            self.known_medications = await self.client.active_medications()
        except GuavaAuthError:
            raise
        except GuavaError as err:
            _LOGGER.warning("Could not read the medication list: %s", err)

    def all_names(self) -> list[str]:
        return [*self.entry.options.get(CONF_MEDICATIONS, []), *self.known_medications]


type GuavaConfigEntry = ConfigEntry[GuavaProfile]


def _target(hass: HomeAssistant, entry_id: str | None) -> GuavaProfile:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id is not None:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(f"No loaded Guava profile {entry_id}")
    if not entries:
        raise ServiceValidationError("No Guava profile is loaded")
    if len(entries) > 1:
        raise ServiceValidationError(
            "More than one Guava profile is set up: choose one with config_entry_id"
        )
    return entries[0].runtime_data


async def _handle_log_medication(call: ServiceCall) -> None:
    profile = _target(call.hass, call.data.get("config_entry_id"))
    requested = call.data["medication"]
    name = resolve_medication(requested, profile.all_names())
    if name is None:
        try:
            await profile.refresh_known()
        except GuavaAuthError as err:
            profile.entry.async_start_reauth(call.hass)
            raise HomeAssistantError(
                f"Guava needs re-authorising for {profile.entry.title}"
            ) from err
        name = resolve_medication(requested, profile.all_names())
    if name is None:
        # Guava would create a new medication from the name, and the API cannot delete it.
        if not call.data["allow_new"]:
            raise ServiceValidationError(
                f"{requested} is not an active medication on {profile.entry.title}. "
                "Set allow_new to create it."
            )
        name = requested
    await profile.log_dose(
        name,
        quantity=call.data.get("quantity"),
        notes=call.data.get("notes"),
        when=call.data.get("time"),
        bypass_cooldown=call.data["bypass_cooldown"],
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the action once for all profiles."""
    hass.services.async_register(
        DOMAIN, SERVICE_LOG_MEDICATION, _handle_log_medication, schema=SERVICE_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GuavaConfigEntry) -> bool:
    """Set up one person's profile."""
    store = token_store(hass, entry.entry_id)
    token = await store.async_load() or entry.data[CONF_TOKEN]
    client = GuavaClient(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
        token,
        store.async_save,
    )
    profile = GuavaProfile(hass, entry, client)
    try:
        await profile.refresh_known()
    except GuavaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err

    entry.runtime_data = profile
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _options_updated(hass: HomeAssistant, entry: GuavaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GuavaConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GuavaConfigEntry) -> None:
    await token_store(hass, entry.entry_id).async_remove()
