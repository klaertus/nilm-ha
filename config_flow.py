"""Config flow for NILM integration."""
import logging
from typing import Any

import voluptuous as vol

from .helpers import is_safe_name

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .backend import NilmBackend
from .const import (
    DOMAIN,
    CONF_DATA_PUSH_INTERVAL,
    CONF_HOST,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_SCAN_INTERVAL,
    CONF_MODEL_NAME,
    CONF_SAMPLING_RATE,
    CONF_WINDOW_SIZE,
    CONF_ENABLED_APPLIANCES
)

_LOGGER = logging.getLogger(__name__)


class NilmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NILM."""

    VERSION = 1


    def __init__(self) -> None:
        self.host: str = ""
        self.port: int = 8000
        self.power_entity: str = ""
        self.scan_interval: int = 30
        self.data_push_interval: int = 24
        self.models: list[str] = []
        self.model_name: str = ""
        self.sampling_rate: int = 5
        self.window_size: int = 0
        self.appliances_available: list[str] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """First step: Collect host, port, power entity, scan interval and validate."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.host = user_input[CONF_HOST]
            self.port = user_input[CONF_PORT]
            self.power_entity = user_input[CONF_POWER_ENTITY]
            self.scan_interval = user_input[CONF_SCAN_INTERVAL]
            self.data_push_interval = user_input[CONF_DATA_PUSH_INTERVAL]
            

            backend = NilmBackend(f"http://{self.host}:{self.port}")
            status = await backend.get_status()
            if status:
                self.models = await backend.get_models()
                return await self.async_step_model()
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default="localhost"): str,
                    vol.Required(CONF_PORT, default="8000"): vol.Coerce(int),
                    vol.Required(CONF_POWER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor", device_class="power"
                        )
                    ),
                    vol.Required(CONF_SCAN_INTERVAL, default=30): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=5, max=300, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                    ),
                    vol.Required(CONF_DATA_PUSH_INTERVAL, default=24): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=1, max=168, step=1, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )


    async def async_step_model(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Second step: Let the user pick a model from a scrollable list (or create a new one)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            chosen_model = user_input.get(CONF_MODEL_NAME, "")
            if chosen_model == "__new_model__":
                return await self.async_step_new_model()
            self.model_name = chosen_model
            return await self.load_model_and_proceed(errors)

        if not self.models:
            return await self.async_step_new_model()

        options: list[selector.SelectOptionDict] = [
            selector.SelectOptionDict(value="__new_model__", label="+ Create new model"),
        ]
        for model in self.models:
            options.append(selector.SelectOptionDict(value=model, label=model))

        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MODEL_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_new_model(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Third step: Ask the user for a name when creating a new model."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.model_name = str(user_input.get("new_model_name", "")).strip()
            if not self.model_name:
                errors["base"] = "new_model_name_required"
            elif not is_safe_name(self.model_name):
                errors["base"] = "invalid_model_name"
            else:
                return await self.load_model_and_proceed(errors)

        return self.async_show_form(
            step_id="new_model",
            data_schema=vol.Schema(
                {
                    vol.Required("new_model_name", default=""): str,
                }
            ),
            errors=errors,
        )

    async def load_model_and_proceed(self, errors: dict[str, str]) -> FlowResult:
        """Load the chosen model on the server and advance to the appliances step."""
        backend = NilmBackend(f"http://{self.host}:{self.port}")

        server_status = await backend.get_status()
        self.sampling_rate = server_status.get("sampling_rate", 5)
        self.window_size = server_status.get("window_size", 599)

        if not await backend.load_model(self.model_name):
            errors["base"] = "model_load_failed"
            return await self.async_step_model(None)
        return await self.async_step_appliances()

    async def async_step_appliances(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Fourth step: Let the user choose which appliances to enable from the loaded model."""
        errors: dict[str, str] = {}

        if user_input is not None:
            enabled = user_input.get(CONF_ENABLED_APPLIANCES, self.appliances_available)
            if not isinstance(enabled, list):
                enabled = list(enabled)
            return self._create_entry(enabled)

        raw = await NilmBackend(f"http://{self.host}:{self.port}").get_appliances()
        self.appliances_available = [
            (appliance["name"] if isinstance(appliance, dict) else str(appliance)) for appliance in raw
        ]
        if not self.appliances_available:
            return self._create_entry([])

        options = [selector.SelectOptionDict(value=appliance, label=appliance.replace("_", " ").title()) for appliance in self.appliances_available]

        return self.async_show_form(
            step_id="appliances",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENABLED_APPLIANCES,
                        default=self.appliances_available,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    def _create_entry(self, enabled_appliances: list) -> FlowResult:
        return self.async_create_entry(
            title=f"NILM ({self.host}:{self.port})",
            data={
                CONF_HOST: self.host,
                CONF_PORT: self.port,
                CONF_POWER_ENTITY: self.power_entity,
                CONF_SCAN_INTERVAL: self.scan_interval,
                CONF_DATA_PUSH_INTERVAL: self.data_push_interval,
                CONF_MODEL_NAME: self.model_name,
                CONF_SAMPLING_RATE: self.sampling_rate,
                CONF_WINDOW_SIZE: self.window_size,
                CONF_ENABLED_APPLIANCES: enabled_appliances,
            },
        )



