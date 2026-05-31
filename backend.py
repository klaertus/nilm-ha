"""NILM backend client."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class NilmBackend:
    """Async HTTP client for the NILM model server."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    # Some helper functions to make the code more readable.

    async def _get(self, path: str, timeout: int = 5) -> Any:
        """GET and return parsed JSON, or None on error."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}{path}", timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                    if response.status == 200:
                        return await response.json()
                    _LOGGER.warning(f"NILM: GET {path} - HTTP {response.status}")
        except Exception as exc:
            _LOGGER.warning(f"NILM: GET {path} failed. Details: {exc}")
        return None

    async def _request(self, method: str, path: str, json: dict | None = None, timeout: int = 15) -> tuple[int, dict]:
        """Generic request : returns (status_code, body). Raises on network/parse error."""
        async with aiohttp.ClientSession() as session:
            async with session.request(method, f"{self.base_url}{path}", json=json, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                return response.status, await response.json()

    # Read operations

    async def get_status(self) -> dict:
        """GET /status"""
        return await self._get("/status") or {}

    async def get_models(self) -> list[str]:
        """GET /models"""
        data = await self._get("/models")
        if not isinstance(data, dict):
            return []
        return [model["name"] if isinstance(model, dict) else str(model) for model in data.get("models", [])]

    async def get_appliances(self) -> list:
        """GET /appliances : returns raw appliance list."""
        data = await self._get("/appliances")
        return data.get("appliances", []) if isinstance(data, dict) else []

    async def get_appliance_names(self) -> list[str]:
        return [appliance["name"] if isinstance(appliance, dict) else str(appliance) for appliance in await self.get_appliances()]

    # Write operations

    async def load_model(self, name: str) -> bool:
        """PUT /models/{name} : declare or load a model.  Returns True on success."""
        try:
            status, body = await self._request("PUT", f"/models/{name}", timeout=30)
            if status == 200:
                _LOGGER.info(f"NILM: Model '{name}' loaded")
                return True
            _LOGGER.warning(f"NILM: PUT /models/{name} - HTTP {status}")
        except Exception as exc:
            _LOGGER.warning(f"NILM: Could not load model '{name}'. Details: {exc}")
        return False

    async def predict(self, power_window: list[float]) -> tuple[int, dict]:
        """POST /prediction.  Raises aiohttp.ClientError on network failure."""
        return await self._request("POST", "/prediction", {"power_window": power_window}, timeout=10)

    async def train(self, configs: dict) -> tuple[int, dict]:
        """POST /training : async job, returns 202."""
        return await self._request("POST", "/training", {"configs": configs}, timeout=30)

    async def finetune(self, configs: dict) -> tuple[int, dict]:
        """POST /finetuning : async job, returns 202."""
        return await self._request("POST", "/finetuning", {"configs": configs}, timeout=60)

    async def finetune_delete(self, name: str) -> tuple[int, dict]:
        """DELETE /finetune/{name} : revert appliance to last trained checkpoint."""
        return await self._request("DELETE", f"/finetune/{name}", timeout=15)

    async def add_data(self, total: list, appliances: dict) -> tuple[int, dict]:
        """POST /data : store a batch of power readings. Returns 201."""
        return await self._request("POST", "/data", {"total": total, "appliances": appliances}, timeout=30)

    async def appliance_add(self, appliance: str, threshold: float | None, sensitivity: str = "medium") -> tuple[int, dict]:
        """POST /appliances : register a new appliance.  Returns 201."""
        return await self._request("POST", "/appliances", {"appliance": appliance, "threshold": threshold, "sensitivity": sensitivity}, timeout=15)

    async def appliance_params(self, appliance: str, threshold: float | None = None, sensitivity: str | None = None) -> tuple[int, dict]:
        """PATCH /appliances/{name} : update threshold and/or sensitivity."""
        payload: dict = {"appliance": appliance}
        if threshold is not None:
            payload["threshold"] = threshold
        if sensitivity is not None:
            payload["sensitivity"] = sensitivity
        return await self._request("PATCH", f"/appliances/{appliance}", payload, timeout=15)

    async def appliance_remove(self, appliance: str) -> tuple[int, dict]:
        """DELETE /appliances/{name}"""
        return await self._request("DELETE", f"/appliances/{appliance}", timeout=15)

    async def set_parameters(self, parameters: dict) -> tuple[int, dict]:
        """PATCH /configuration"""
        return await self._request("PATCH", "/configuration", {"parameters": parameters}, timeout=15)

    async def reset_data(self) -> tuple[int, dict]:
        """DELETE /data : wipe all data from DB and reset model state."""
        return await self._request("DELETE", "/data", timeout=30)

    async def calibrate(self, appliances: list[str] | None = None) -> tuple[int, dict]:
        """POST /calibrate : recompute agg_mean/agg_std from stored aggregate data."""
        payload: dict = {}
        if appliances:
            payload["appliances"] = appliances
        return await self._request("POST", "/calibrate", payload, timeout=30)

    async def calibrate_delete(self, appliance: str | None = None) -> tuple[int, dict]:
        """DELETE /calibrate/{name} or DELETE /calibrate : revert calibration."""
        path = f"/calibrate/{appliance}" if appliance else "/calibrate"
        return await self._request("DELETE", path, timeout=15)
