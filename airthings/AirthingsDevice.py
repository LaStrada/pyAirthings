from dataclasses import dataclass
from .AirthingsSensor import AirthingsSensor


@dataclass
class AirthingsDevice:
    """Airthings device."""

    serial_number: str
    home: str | None
    name: str
    type: str
    sensors: list[AirthingsSensor] | None

    @classmethod
    def init_from_response(
        cls,
        response: dict,
    ) -> "AirthingsDevice":
        """Class method."""
        return cls(
            serial_number=response.get("serialNumber"),
            home=response.get("home"),
            name=response.get("name"),
            type=response.get("type"),
            sensors=[],
        )

    def update_sensors(
        self,
        sensors: list[dict[str, float | str | None]],
        battery: float | None = None,
    ) -> None:
        """Update sensors."""
        self.sensors = [
            AirthingsSensor.init_from_response(sensor) for sensor in sensors
        ]
        if battery is not None:
            self.sensors.append(
                AirthingsSensor(type="battery", value=battery, unit="%")
            )

    @property
    def sensor_types(self) -> set[str]:
        """Sensor types."""
        return set(self.sensors)

    @property
    def is_hub(self) -> bool:
        """Return True if device is a hub without any sensors."""
        return self.type.upper() == "HUB"

    @property
    def type_name(self) -> str:
        """Return type name."""
        types = {
            "AP_1": "Renew",
            "HUB": "Hub",
            "WAVE": "Wave gen 1",
            "WAVE_GEN2": "Wave Radon",
            "WAVE_MINI": "Wave Mini",
            "WAVE_PLUS": "Wave Plus",
            "VIEW_PLUS": "View Plus",
            "VIEW_RADON": "View Radon",
            "VIEW_POLLUTION": "View Pollution",
            "RAVEN_RADON": "Corentium Home 2",
            "WAVE_ENHANCE": "Wave Enhance",
        }
        return types.get(self.type.upper(), self.type.title())
