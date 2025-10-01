"""Support for Airthings sensor."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession
import async_timeout

_LOGGER = logging.getLogger(__name__)

API_URL = "https://consumer-api.airthings.com/v1/"
TIMEOUT = 10


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
    ) -> AirthingsDevice:
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


class AirthingsError(Exception):
    """General Airthings exception occurred."""


class AirthingsConnectionError(AirthingsError):
    """ConnectionError Airthings occurred."""


class AirthingsAuthError(AirthingsError):
    """AirthingsAuthError Airthings occurred."""


class Airthings:
    """Airthings data handler."""

    def __init__(
        self,
        client_id: str,
        secret: str,
        websession: ClientSession
    ) -> None:
        """Init Airthings data handler."""
        self._client_id = client_id
        self._secret = secret
        self._websession = websession
        self._access_token = None
        self._accounts: list[str] = []
        self._devices: dict[str, AirthingsDevice] = {}

    async def update_devices(self, is_metric: bool) -> dict[str, AirthingsDevice]:
        """Update and return latest device data per location."""
        if not self._accounts:
            accounts = await self.get_accounts()
            if accounts is None:
                raise AirthingsError("No accounts found")
            self._accounts = accounts
        
        if not self._devices:
            devices = await self.get_devices(self._accounts)
            if devices is None:
                return {}
            self._devices = devices

        # TODO: Clean up sensors before updating

        # Update sensors
        sensors_data: list[dict[str, Any]] = []
        for account_id in self._accounts:
            sensors_data += await self.get_sensors(
                account_id, is_metric=is_metric
            ) or []

        # Map sensors_data to devices
        for device in self._devices.values():
            device.sensors = {}
            for sensor in sensors_data:
                if sensor.get("serialNumber") == device.serial_number:
                    logging.warning("Found sensor data for device %s: %s", device.serial_number, sensor)
        return self._devices

    async def get_accounts(self) -> dict[str] | None:
        """Get account information."""
        response = await self._request(API_URL + "accounts")
        if response is None:
            return None

        json_data = await response.json()
        if json_data is None:
            return None
        return [account["id"] for account in json_data.get("accounts", [])]

    async def get_devices(
        self,
        account_ids: list[str]
    ) -> dict[str, AirthingsDevice] | None:
        """Get devices for account."""
        devices: dict[str, AirthingsDevice] = {}

        for account_id in account_ids:
            response = await self._request(API_URL + f"accounts/{account_id}/devices")

            if response is None:
                continue

            json_data = await response.json()
            if json_data is None:
                continue

            for device in json_data.get("devices", []):
                sn = device.get("serialNumber")
                if sn:
                    try:
                        devices[sn] = AirthingsDevice.init_from_response(device)
                        logging.debug("Initialized AirthingsDevice: %s", devices[sn])
                    except Exception as e:
                        _LOGGER.error("Error initializing AirthingsDevice: %s", e)
                        continue

        return devices

    async def get_sensors(
        self,
        account_id: str,
        page_number: int = 1,
        is_metric: bool = True,
    ) -> dict[str, Any] | None:
        """Get sensors for device."""
        response = await self._request(
            API_URL +
            f"accounts/{account_id}/sensors" +
            f"?pageNumber={page_number}&isMetric={is_metric}",
        )
        if response is None:
            return None

        # Check if there is a next page
        json_data = await response.json()
        if json_data is None:
            logging.error("No JSON data received for sensors of account %s", account_id)
            return None
        
        results = json_data.get("results")
        if results is None:
            logging.error("No results in JSON data for sensors of account %s", account_id)
            return None

        if json_data.get("hasNext"):
            logging.warning("Fetching next page of sensors for account %s", account_id)
            return json_data + await self.get_sensors(
                account_id, page_number + 1, is_metric
            )
        logging.info("Fetched %d devices for account %s on page %d", len(results), account_id, page_number)
        logging.info("Raw sensor data: %s", results)
        return results

    async def _request(
        self,
        url: str,
        retry: int = 3,
    ) -> ClientResponse | None:
        """Make a request to Airthings API."""
        _LOGGER.debug("Request %s", url)

        if self._access_token is None:
            self._access_token = await get_token(
                self._websession, self._client_id, self._secret
            )
            if self._access_token is None:
                return None

        headers = {"Authorization": f"Bearer {self._access_token}"}

        try:
            async with async_timeout.timeout(TIMEOUT):
                response = await self._websession.get(url, headers=headers)
            if response.status != 200:
                if retry > 0 and response.status != 429:
                    self._access_token = None
                    return await self._request(url, retry=retry - 1)
                logging
                raise AirthingsError(
                    f"Error connecting to Airthings, url: {url}, "
                    f"status: {response.status}, "
                    f"headers: {response.headers}, "
                    f"response: {response.reason}"
                )
        except ClientError as err:
            self._access_token = None
            _LOGGER.error("Error connecting to Airthings: %s", err, exc_info=True)
            raise AirthingsError from err
        except asyncio.TimeoutError as err:
            self._access_token = None
            if retry > 0:
                _LOGGER.warning(
                    "Timeout talking to Airthings. Retrying… (%d left)", retry
                )
                return await self._request(url, retry=retry - 1)
            _LOGGER.error("Timed out when connecting to Airthings")
            raise AirthingsError from err
        return response


async def get_token(
    websession: ClientSession,
    client_id: str,
    secret: str,
    retry: int = 3,
    timeout: float = TIMEOUT,
) -> str | None:
    """Get token for Airthings."""
    try:
        async with async_timeout.timeout(timeout):
            response = await websession.post(
                "https://accounts-api.airthings.com/v1/token",
                headers={
                    "Content-type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret,
                },
            )
    except ClientError as err:
        if retry > 0:
            _LOGGER.warning(
                "Token request failed (%s). Retrying… (%d left)", err, retry
            )
            return await get_token(websession, client_id, secret, retry - 1, timeout)
        _LOGGER.error("Error getting token Airthings: %s", err, exc_info=True)
        raise AirthingsConnectionError from err
    except asyncio.TimeoutError as err:
        if retry > 0:
            _LOGGER.warning("Token request timed out. Retrying… (%d left)", retry)
            return await get_token(websession, client_id, secret, retry - 1, timeout)
        _LOGGER.error("Timed out when connecting to Airthings for token")
        raise AirthingsConnectionError from err
    if response.status != 200:
        _LOGGER.error(
            "Airthings: Failed to login to retrieve token: %s %s",
            response.status,
            response.reason,
        )
        raise AirthingsAuthError(f"Failed to login to retrieve token {response.reason}")

    data = await response.json()
    logging.debug("Airthings token response: %s", data)
    return data.get("access_token")
