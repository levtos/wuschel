"""Optional Wuschel values; unknown contract values never reuse the last state."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.util import slugify

from .projection import Projection

KEY = "traber_projection"


async def async_setup_entry(hass, entry, async_add_entities):
    projection: Projection = hass.data[KEY]
    known = set()

    @callback
    def update():
        additions = []
        for contract_id in projection.rows:
            identity = (projection.installation_id, contract_id)
            if identity not in known:
                known.add(identity)
                additions.append(ContractSensor(projection, *identity))
        if additions:
            async_add_entities(additions)

    projection.listeners.add(update)
    entry.async_on_unload(lambda: projection.listeners.discard(update))
    update()


class ContractSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, projection, installation_id, contract_id):
        self.projection = projection
        self.installation_id = installation_id
        self.contract_id = contract_id
        self._attr_unique_id = f"{installation_id}:{contract_id}"
        # HA 2026.10: an entity suggests its ID by presetting entity_id (unique_id keeps it stable).
        self.entity_id = f"sensor.wuschel_{slugify(contract_id)}"

    @property
    def row(self):
        return (
            self.projection.rows.get(self.contract_id, {})
            if self.projection.installation_id == self.installation_id
            else {}
        )

    @property
    def name(self):
        return self.row.get("name", self.contract_id)

    @property
    def available(self):
        return self.projection.connected and bool(self.row)

    @property
    def native_value(self):
        value = self.row.get("value")
        return (
            value
            if self.row.get("status") in {"valid", "held"}
            and (not isinstance(value, str) or len(value) <= 255)
            else None
        )

    @property
    def native_unit_of_measurement(self):
        return self.row.get("unit")

    @property
    def extra_state_attributes(self):
        return {
            "contract_id": self.contract_id,
            "status": self.row.get("status", "unknown"),
            "quality": self.row.get("quality", "degraded"),
            "since_at": self.row.get("since_at"),
        }

    async def async_added_to_hass(self):
        self.projection.listeners.add(self.async_write_ha_state)
        self.async_on_remove(lambda: self.projection.listeners.discard(self.async_write_ha_state))
