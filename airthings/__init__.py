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
class AirthingsDevice:
    """Airthings device."""

    device_id: str
    name: str
    sensors: dict[str, float | None]
    is_active: bool
    location_name: str
    device_type: str | None
    product_name: str | None

    @classmethod
    def init_from_response(
        cls,
        response: dict,
        location_name: str,
        device: dict[str, Any],
    ) -> AirthingsDevice:
        """Class method."""
        return cls(
            device_id=response.get("id"),
            name=response.get("segment").get("name"),
            sensors=response.get("data"),
            is_active=response.get("segment").get("isActive"),
            location_name=location_name,
            device_type=device.get("deviceType"),
            product_name=device.get("productName"),
        )

    @property
    def sensor_types(self) -> set[str]:
        """Sensor types."""
        return set(self.sensors)


class AirthingsError(Exception):
    """General Airthings exception occurred."""


class AirthingsConnectionError(AirthingsError):
    """ConnectionError Airthings occurred."""


class AirthingsAuthError(AirthingsError):
    """AirthingsAuthError Airthings occurred."""


class Airthings:
    """Airthings data handler."""

    def __init__(self, client_id: str, secret: str, websession: ClientSession) -> None:
        """Init Airthings data handler."""
        self._client_id = client_id
        self._secret = secret
        self._websession = websession
        self._access_token = None
        self._locations = []
        self._devices = {}

    async def update_devices(self) -> dict[str, AirthingsDevice]:
        """Update and return latest device data per location."""
        if not self._locations:
            response = await self._request(API_URL + "locations")
            if response is None:
                return {}
            json_data = await response.json()
            self._locations = []
            for location in json_data.get("locations"):
                self._locations.append(AirthingsLocation.init_from_response(location))
        if not self._devices:
            response = await self._request(API_URL + "devices")
            if response is None:
                return {}
            json_data = await response.json()
            self._devices = {}
            for device in json_data.get("devices"):
                self._devices[device["id"]] = device
        res = {}
        for location in self._locations:
            if not location.location_id:
                continue

            response = await self._request(
                API_URL + f"locations/{location.location_id}/latest-samples"
            )
            if response is None:
                continue
            json_data = await response.json()
            if json_data is None:
                continue
            if devices := json_data.get("devices"):
                for device in devices:
                    device_id = device.get("id")
                    res[device_id] = AirthingsDevice.init_from_response(
                        device, location.name, self._devices.get(device_id)
                    )
            else:
                _LOGGER.debug("No devices in location '%s'", location.name)
        return res

    async def _request(
        self,
        url: str,
        json_data: dict[str, Any] | None = None,
        retry: int = 3,
    ) -> ClientResponse | None:
        """Make a request to Airthings API."""
        _LOGGER.debug("Request %s (retry=%s) payload=%s", url, retry, json_data)

        if self._access_token is None:
            self._access_token = await get_token(
                self._websession, self._client_id, self._secret
            )
            if self._access_token is None:
                return None

        headers = {"Authorization": f"Bearer {self._access_token}"}

        try:
            async with async_timeout.timeout(TIMEOUT):
                if json_data:
                    response = await self._websession.post(
                        url, json=json_data, headers=headers
                    )
                else:
                    response = await self._websession.get(url, headers=headers)
            if response.status != 200:
                if retry > 0 and response.status != 429:
                    self._access_token = None
                    return await self._request(url, json_data, retry=retry - 1)
                _LOGGER.error(
                    "Error connecting to Airthings, response: %s %s",
                    response.status,
                    response.reason,
                )
                raise AirthingsError(
                    f"Error connecting to Airthings, response: {response.reason}"
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
                return await self._request(url, json_data, retry=retry - 1)
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
    return data.get("access_token")
