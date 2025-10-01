from dataclasses import dataclass
from .AirthingsSensor import AirthingsSensor


@dataclass
class AirthingsSensor:
    """Airthings sensor."""

    type: str
    value: float | str | None
    unit: str | None

    @classmethod
    def init_from_response(
        cls,
        response: dict[str, float | str | None],
    ) -> list[AirthingsSensor]:
        """Initialize sensors from response."""
        return cls(
            type=response.get("sensorType"),
            value=response.get("value"),
            unit=response.get("unit"),
        )
