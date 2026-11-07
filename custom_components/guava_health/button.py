"""One button per chosen medication, plus the log_dose entity action."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.button import ButtonEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_platform, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import GuavaConfigEntry, GuavaProfile
from .const import CONF_MEDICATIONS, DOMAIN, EVENT_DOSE_LOGGED, SERVICE_LOG_DOSE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GuavaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add buttons and drop those of medications no longer chosen."""
    buttons = [
        GuavaDoseButton(entry.runtime_data, name)
        for name in entry.options.get(CONF_MEDICATIONS, [])
    ]
    wanted = {button.unique_id for button in buttons}
    registry = er.async_get(hass)
    for existing in er.async_entries_for_config_entry(registry, entry.entry_id):
        if existing.unique_id not in wanted:
            registry.async_remove(existing.entity_id)
    async_add_entities(buttons)

    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_LOG_DOSE,
        {
            vol.Optional("quantity", default=1): vol.All(
                vol.Coerce(float), vol.Range(min=0.001, max=1000)
            )
        },
        "async_log_dose",
    )


class GuavaDoseButton(ButtonEntity):
    """Logs a dose of one medication: quantity 1 on press, any through log_dose."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pill"

    def __init__(self, profile: GuavaProfile, medication: str) -> None:
        self._profile = profile
        self._medication = medication  # Exact stored name, trailing spaces included.
        self._last_logged: str | None = None
        self._last_quantity: float | None = None
        entry = profile.entry
        self._attr_name = f"Log {medication.strip()}"
        self._attr_unique_id = f"{entry.unique_id}_{slugify(medication)}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Guava {entry.title}",
            manufacturer="Guava Health (unofficial)",
            model="Medication dose logger",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Doses Guava accepted. The button's own state also counts failed presses."""
        return {"last_logged": self._last_logged, "last_quantity": self._last_quantity}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._last_logged = last.attributes.get("last_logged")
            self._last_quantity = last.attributes.get("last_quantity")
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_DOSE_LOGGED, self._dose_logged)
        )

    @callback
    def _dose_logged(self, event: Event) -> None:
        data = event.data
        if (
            data["config_entry_id"] == self._profile.entry.entry_id
            and data["medication"] == self._medication.strip()
        ):
            self._last_logged = data["time"]
            self._last_quantity = data["quantity"]
            self.async_write_ha_state()

    async def async_press(self) -> None:
        await self._profile.log_dose(self._medication)

    async def async_log_dose(self, quantity: float) -> None:
        await self._profile.log_dose(self._medication, quantity=quantity)
