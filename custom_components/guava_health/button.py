"""One button per chosen medication."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import GuavaConfigEntry, GuavaProfile
from .const import CONF_MEDICATIONS, DOMAIN


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


class GuavaDoseButton(ButtonEntity):
    """Logs one dose (quantity 1) of a medication."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pill"

    def __init__(self, profile: GuavaProfile, medication: str) -> None:
        self._profile = profile
        self._medication = medication  # Exact stored name, trailing spaces included.
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

    async def async_press(self) -> None:
        await self._profile.log_dose(self._medication)
