"""HA-side HTTP views for the NILM component."""
from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .backend import NilmBackend
from .binary_sensor import NilmBinarySensor
from .const import DOMAIN
from .data import async_send_data
from .labels import (
    async_load_labels,
    async_save_labels,
)
from .sensor import NilmPowerSensor

_LOGGER = logging.getLogger(__name__)


# Helpers

def get_backend(hass: HomeAssistant) -> NilmBackend | None:
    """Return the shared NilmBackend instance, or None if not set up yet."""
    return hass.data.get(DOMAIN, {}).get("backend")


async def refresh_coordinator(hass: HomeAssistant) -> None:
    """Request a coordinator refresh so entities pick up a label change."""
    coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
    if coordinator:
        await coordinator.async_request_refresh()


class StatusView(HomeAssistantView):
    """GET /status : {is_training, is_finetuning, model_name, etc}"""

    url = "/api/nilm/model_status"
    name = "api:nilm:model_status"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            result = await backend.get_status()
        except Exception as exc:
            _LOGGER.warning(f"NILM: model_status fetch failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)

        result["power_buffer_size"] = len(hass.data.get(DOMAIN, {}).get("raw_events", []))
        return self.json(result)


class AppliancesView(HomeAssistantView):
    """Proxy GET /appliances on the model server.
    
    Return:
    {
        "appliances": [
            "fridge",
            "kettle",
            ...
        ]
    }
    """


    url = "/api/nilm/appliances"
    name = "api:nilm:appliances"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"appliances": []})

        try:
            return self.json({"appliances": await backend.get_appliances()})
        except Exception as exc:
            _LOGGER.warning(f"NILM: /appliances fetch failed. Details: {exc}")
            return self.json({"appliances": []})




class DevicesView(HomeAssistantView):
    """Return all appliances with their current predicted watts and state.

    Return : {
    "appliances": {
        "fridge": {
            "state": 1,  # on/off state predicted by the model
            "power": 50.0,  # predicted power in watts
            "linked_entity": "switch.fridge_power"  # optional linked HA entity from user
            },
        },
    "total_power": 123.45,  # from the linked power sensor (if configured)
    "predicted_power": 120.00,  # sum of all predicted appliance power
    }
    """

    url = "/api/nilm/devices"
    name = "api:nilm:devices"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        domain_data = hass.data.get(DOMAIN, {})
        coordinator = domain_data.get("coordinator")
        power_entity = domain_data.get("power_entity")

        # Base: all labeled appliances, including those without predictions yet
        labels = await async_load_labels(hass)
        appliances: dict = {
            device_id: {
                "state": 0,
                "power": 0.0,
                "linked_entity": info.get("linked_entity"),
            }
            for device_id, info in labels.items()
        }

        # Override with live coordinator predictions where available
        if coordinator and coordinator.data:
            for device_id, data in coordinator.data.items():
                if device_id in appliances:
                    appliances[device_id]["state"] = data.get("state", 0)
                    appliances[device_id]["power"] = data.get("power", 0.0)
                else:
                    # Predicted by backend but not yet labeled by user
                    appliances[device_id] = {
                        "state": data.get("state", 0),
                        "power": data.get("power", 0.0),
                        "linked_entity": None,
                    }

        total_power = None
        if power_entity:
            try:
                power_state = hass.states.get(power_entity)
                if power_state:
                    total_power = float(power_state.state)
            except (ValueError, TypeError):
                pass

        predicted_power = round(sum(info.get("power", 0.0) for info in appliances.values()), 3) if appliances else None

        return self.json({
            "appliances": appliances,
            "total_power": total_power,
            "predicted_power": predicted_power
        })



class FinetuneView(HomeAssistantView):
    """Trigger finetuning on the model server.
    
    Return: 
        {
            "status": "success",
            "n_corrections": 5,  # number of corrections accumulated in this finetune session
            "message": "Finetune launched successfully"
        }
    
    """

    url = "/api/nilm/finetune"
    name = "api:nilm:finetune"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            body = {}

        try:
            status, resp_body = await backend.finetune(body.get("configs") or {})
            _LOGGER.info(f"NILM: /finetune response: {resp_body}")
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /finetune proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)




class FinetuneResetView(HomeAssistantView):
    """Delete the active finetune and reset all accumulated corrections.
    
    Return:
            {"status":"ok", "deleted": ["kettle", "fridge"] }

    """

    url = "/api/nilm/finetune_delete"
    name = "api:nilm:finetune_delete"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            body = {}

        name = body.get("finetune") or body.get("name") or body.get("appliance")
        if not name:
            return self.json({"error": "appliance name required"}, status_code=400)

        try:
            status, resp_body = await backend.finetune_delete(name)
            _LOGGER.info(f"NILM: DELETE /finetune/{name} response: {resp_body}")
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: finetune_delete proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)




class TrainStatusView(HomeAssistantView):
    """Poll training status from the backend."""

    url = "/api/nilm/train_status"
    name = "api:nilm:train_status"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            data = await backend.get_status()
        except Exception as exc:
            _LOGGER.warning(f"NILM: train_status fetch failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)

        # Well, for now this is a rudimentary way to estimate training progress.  
        # COuld be improved in a future release by adding a dedicated /training_status endpoint on the backend that returns a proper progress percentage.
        progress = 0
        if data.get("is_training"):
            progress = 20
        elif data.get("training_results"):
            progress = 100

        return self.json({
            "is_training": bool(data.get("is_training")),
            "is_finetuning": bool(data.get("is_finetuning")),
            "progress": progress,
            "samples_count": data.get("samples_count", 0),
            "training_results": data.get("training_results") or {},
            "finetuning_results": data.get("finetuning_results") or {},
        })




def register_appliance_entities(hass: HomeAssistant, appliance_id: str) -> None:
    """Add HA binary_sensor + power sensor entities for a newly added appliance."""
    if not appliance_id:
        return

    entry_id = hass.data.get(DOMAIN, {}).get("entry_id")
    if not entry_id:
        return

    coordinator = hass.data[DOMAIN].get("coordinator")
    if not coordinator:
        return

    # Ensure the coordinator won't filter out this appliance's predictions
    enabled = hass.data[DOMAIN].get("enabled_appliances")
    if isinstance(enabled, list) and appliance_id not in enabled:
        enabled.append(appliance_id)
    # Just in case, an NILM appliance is composed of a binary sensor and a power sensor
    known_binary_sensors = hass.data[DOMAIN].setdefault("known_binary_sensors", set())
    known_power_sensors = hass.data[DOMAIN].setdefault("known_power_sensors", set())

    add_binary_sensors = hass.data[DOMAIN].get("add_entities_binary_sensor")
    add_power_sensors = hass.data[DOMAIN].get("add_entities_sensor")

    new_binary_sensors = []
    new_power_sensors = []

    if appliance_id not in known_binary_sensors and add_binary_sensors:
        known_binary_sensors.add(appliance_id)
        new_binary_sensors.append(NilmBinarySensor(coordinator, appliance_id, entry_id))

    if appliance_id not in known_power_sensors and add_power_sensors:
        known_power_sensors.add(appliance_id)
        new_power_sensors.append(NilmPowerSensor(coordinator, appliance_id, entry_id))

    if new_binary_sensors:
        add_binary_sensors(new_binary_sensors, True)
    if new_power_sensors:
        add_power_sensors(new_power_sensors, True)

    _LOGGER.info(f"NILM: Registered HA entities for new appliance '{appliance_id}'")


async def unregister_appliance_entities(hass: HomeAssistant, appliance_id: str) -> None:
    """Remove HA entities for a deleted appliance from the entity registry."""
    if not appliance_id:
        return

    entry_id = hass.data.get(DOMAIN, {}).get("entry_id")
    if not entry_id:
        return

    registry = er.async_get(hass)

    unique_ids = {
        f"{entry_id}_nilm_{appliance_id}_state",
        f"{entry_id}_nilm_{appliance_id}_power",
    }

    to_remove = [entity.entity_id for entity in registry.entities.values() if entity.unique_id in unique_ids]
    for entity_id in to_remove:
        registry.async_remove(entity_id)

    # Remove from known sets so dynamic discovery won't readd them
    hass.data[DOMAIN].get("known_binary_sensors", set()).discard(appliance_id)
    hass.data[DOMAIN].get("known_power_sensors", set()).discard(appliance_id)

    # Remove from the coordinator's enabled filter
    enabled = hass.data[DOMAIN].get("enabled_appliances")
    if isinstance(enabled, list):
        try:
            enabled.remove(appliance_id)
        except ValueError:
            pass

    _LOGGER.info(f"NILM: Removed HA entities for deleted appliance '{appliance_id}'")


class ApplianceAddView(HomeAssistantView):
    """Proxy POST /appliance/add on backend."""

    url = "/api/nilm/appliance_add"
    name = "api:nilm:appliance_add"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON body"}, status_code=400)

        appliance = body.get("appliance")
        try:
            status, resp_body = await backend.appliance_add(
                appliance,
                body.get("threshold"),
                body.get("sensitivity") or "medium",
            )
            if status == 201:
                register_appliance_entities(hass, appliance)
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /appliance/add proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)


class ApplianceParamsView(HomeAssistantView):
    """Proxy POST /appliance/params : update threshold and/or sensitivity."""

    url = "/api/nilm/appliance_params"
    name = "api:nilm:appliance_params"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON body"}, status_code=400)

        try:
            status, resp_body = await backend.appliance_params(
                body.get("appliance"),
                body.get("threshold"),
                body.get("sensitivity"),
            )
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /appliance/params proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)


class ApplianceRemoveView(HomeAssistantView):
    """Proxy POST /appliance/remove on backend."""

    url = "/api/nilm/appliance_remove"
    name = "api:nilm:appliance_remove"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON body"}, status_code=400)

        appliance = body.get("appliance")
        try:
            status, resp_body = await backend.appliance_remove(appliance)
            if status == 200:
                await unregister_appliance_entities(hass, appliance)
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /appliance/remove proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)


class SetParametersView(HomeAssistantView):
    """Proxy PATCH /configuration on backend.

    """

    url = "/api/nilm/set_parameters"
    name = "api:nilm:set_parameters"
    requires_auth = True

    allowed_params = {"fallback_threshold", "device"}

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON body"}, status_code=400)

        raw = body.get("parameters") or {}
        filtered = {key: value for key, value in raw.items() if key in self.allowed_params}
        rejected = sorted(set(raw) - self.allowed_params)
        if not filtered:
            return self.json(
                {"error": "no settable parameter provided", "allowed": sorted(self.allowed_params), "rejected": rejected},
                status_code=400,
            )

        try:
            status, resp_body = await backend.set_parameters(filtered)
            if rejected:
                resp_body = {**resp_body, "rejected": rejected}
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /set_parameters proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)



class ResetDataView(HomeAssistantView):
    """DELETE /data : wipe all data from the backend DB and reset model state."""

    url = "/api/nilm/reset_data"
    name = "api:nilm:reset_data"
    requires_auth = True

    async def delete(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)
        try:
            status, body = await backend.reset_data()
            return self.json(body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: DELETE /data failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)


class PushDataView(HomeAssistantView):
    """Collect HA history for all linked appliances and push it to /data/add.

    configured data_push_interval, or 6 h).
    """

    url = "/api/nilm/push_data"
    name = "api:nilm:push_data"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        if not get_backend(hass):
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            body = {}

        start_iso: str | None = body.get("start") or None
        end_iso: str | None = body.get("end")   or None

        result = await async_send_data(hass, start_iso=start_iso, end_iso=end_iso)
        status = 502 if "error" in result else 200
        return self.json(result, status_code=status)






class CalibrateView(HomeAssistantView):
    """Proxy POST /calibrate : recompute normalisation stats from stored aggregate data."""

    url = "/api/nilm/calibrate"
    name = "api:nilm:calibrate"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            body = {}

        appliances = body.get("appliances") or None
        try:
            status, resp_body = await backend.calibrate(appliances)
            _LOGGER.info(f"NILM: /calibrate response: {resp_body}")
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /calibrate proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)


class CalibrateDeleteView(HomeAssistantView):
    """Proxy DELETE /calibrate : revert calibration for one appliance or all."""

    url = "/api/nilm/calibrate_delete"
    name = "api:nilm:calibrate_delete"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            body = {}

        appliance = body.get("appliance") or None
        try:
            status, resp_body = await backend.calibrate_delete(appliance)
            _LOGGER.info(f"NILM: /calibrate_delete response: {resp_body}")
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /calibrate_delete proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)


class TrainView(HomeAssistantView):
    """Proxy POST /train on backend."""

    url = "/api/nilm/train"
    name = "api:nilm:train"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        backend = get_backend(hass)
        if not backend:
            return self.json({"error": "backend not configured"}, status_code=503)

        try:
            body = await request.json()
        except Exception:
            body = {}

        try:
            status, resp_body = await backend.train(body.get("configs", {}))
            return self.json(resp_body, status_code=status)
        except Exception as exc:
            _LOGGER.error(f"NILM: /training proxy failed. Details: {exc}")
            return self.json({"error": str(exc)}, status_code=502)


# Label views : operate on the HA local user labels file only, never the
# model server. linked_entity and the display name never reach the backend.

class LinkDeviceView(HomeAssistantView):
    """POST /api/nilm/link_device : link an appliance to an HA power entity."""

    url = "/api/nilm/link_device"
    name = "api:nilm:link_device"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON body"}, status_code=400)

        device_id = body.get("device_id")
        linked_entity = body.get("linked_entity")
        if not device_id or not linked_entity:
            return self.json({"error": "device_id and linked_entity required"}, status_code=400)

        labels = await async_load_labels(hass)
        labels.setdefault(device_id, {})["linked_entity"] = linked_entity
        await async_save_labels(hass, labels)
        _LOGGER.info(f"NILM: '{device_id}' linked to '{linked_entity}'")
        await refresh_coordinator(hass)
        return self.json({"status": "ok", "device_id": device_id, "linked_entity": linked_entity})


class UnlinkDeviceView(HomeAssistantView):
    """POST /api/nilm/unlink_device : remove an appliance's linked HA entity."""

    url = "/api/nilm/unlink_device"
    name = "api:nilm:unlink_device"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "invalid JSON body"}, status_code=400)

        device_id = body.get("device_id")
        if not device_id:
            return self.json({"error": "device_id required"}, status_code=400)

        labels = await async_load_labels(hass)
        unlinked = labels.get(device_id, {}).pop("linked_entity", None) is not None
        if unlinked:
            await async_save_labels(hass, labels)
            _LOGGER.info(f"NILM: '{device_id}' unlinked")
        else:
            _LOGGER.warning(f"NILM: '{device_id}' has no linked entity")
        await refresh_coordinator(hass)
        return self.json({"status": "ok", "device_id": device_id, "unlinked": unlinked})

