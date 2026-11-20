"""Overview sensor: packages in the freezer, the full list as attributes for the dashboard."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SIGNAL_UPDATED
from .manager import Manager


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([StockSensor(entry.runtime_data)])


class StockSensor(SensorEntity):
    """State = number of packages in the freezer."""

    _attr_has_entity_name = False
    _attr_name = "Vriezer voorraad"
    _attr_unique_id = "vriezer_voorraad"
    _attr_icon = "mdi:fridge-outline"
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "pakjes"
    # the lists change often and are large: keep them out of the recorder
    _unrecorded_attributes = frozenset(
        {"pakjes", "recent_uit", "categorieen", "qr", "labelprinter", "bonprinter"}
    )

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._update)
        )

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return sum(1 for p in self._manager.data["pakjes"].values() if not p["uit"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._manager.snapshot()
