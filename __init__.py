"""NILM - Non-Intrusive Load Monitoring custom component for Home Assistant."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp
import voluptuous as vol

from .backend import NilmBackend

from homeassistant.components.frontend import async_remove_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import (
    async_register_panel as async_register_custom_panel,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.util import dt as dt_util

from .helpers import predict_status_message
from .api import (
    ApplianceAddView,
    ApplianceParamsView,
    ApplianceRemoveView,
    AppliancesView,
    CalibrateView,
    CalibrateDeleteView,
    DevicesView,
    FinetuneResetView,
    FinetuneView,
    LinkDeviceView,
    PushDataView,
    ResetDataView,
    SetParametersView,
    StatusView,
    TrainStatusView,
    TrainView,
    UnlinkDeviceView,
)
from .labels import labels_path
from .data import async_send_data
from .const import (
    COMPONENT_VERSION,
    CONF_DATA_PUSH_INTERVAL,
    CONF_HOST,
    CONF_MODEL_NAME,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_SAMPLING_RATE,
    CONF_SCAN_INTERVAL,
    CONF_WINDOW_SIZE,
    CONF_ENABLED_APPLIANCES,
    DOMAIN,
)
from .helpers import parse_ha_states, resample_to_grid, normalize_predictions


_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["binary_sensor", "sensor"]



async def prefill_buffer_from_history(hass):
    """Pre-fill the raw-event buffer with recorder history on startup.

    """
    power_entity = hass.data[DOMAIN]["power_entity"]
    window_size  = hass.data[DOMAIN]["window_size"]
    freq_sample  = hass.data[DOMAIN]["freq_sample"]

    if not power_entity:
        return
    try:

        end = dt_util.utcnow()
        start = end - timedelta(seconds=window_size * freq_sample)

        states = await get_instance(hass).async_add_executor_job(
            lambda: get_significant_states(hass, start, end, [power_entity], minimal_response=True, significant_changes_only=False)
        )

        timestamps, values = parse_ha_states(states.get(power_entity, []))
        if timestamps:
            hass.data[DOMAIN]["raw_events"] = list(zip(timestamps, values))
            _LOGGER.info(f"NILM: Pre-filled raw buffer - {len(timestamps)} change events over {window_size * freq_sample}s")
        else:
            _LOGGER.warning(f"NILM: No history found for {power_entity} in the last {window_size * freq_sample} seconds")

    except Exception as exc:
        _LOGGER.warning(f"NILM: Could not pre-fill buffer. Details: {exc}")


def _register_power_listener(hass, entry):
    """Push every state change of the power entity into the raw-event buffer.

    """
    power_entity = hass.data[DOMAIN]["power_entity"]
    window_size  = hass.data[DOMAIN]["window_size"]
    freq_sample  = hass.data[DOMAIN]["freq_sample"]
    if not power_entity:
        return

    horizon_seconds = window_size * freq_sample

    @callback_state_listener(_LOGGER)
    def _on_change(event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown", None, ""):
            return
        try:
            value = float(new_state.state)
        except (ValueError, TypeError):
            return
        timestamp = new_state.last_changed.timestamp() if new_state.last_changed else dt_util.utcnow().timestamp()

        events = hass.data[DOMAIN].setdefault("raw_events", [])
        events.append((timestamp, value))
        # Trim anything outside the rolling horizon to bound memory.
        cutoff = timestamp - horizon_seconds
        if events and events[0][0] < cutoff:
            hass.data[DOMAIN]["raw_events"] = [event for event in events if event[0] >= cutoff]

    cancel = async_track_state_change_event(hass, [power_entity], _on_change)
    entry.async_on_unload(cancel)


def callback_state_listener(logger):
    """Decorator that wraps a state-change listener to swallow exceptions.

    """
    def _wrap(func):
        @callback
        def _safe(event):
            try:
                func(event)
            except Exception as exc:
                logger.debug(f"NILM: state listener swallowed exception. Details: {exc}")
        return _safe
    return _wrap



async def push_recent_history(hass, hours: float) -> None:
    """Collect the last hours of recorder history and POST it to the backend.

    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    await async_send_data(hass, start_iso=start.isoformat(), end_iso=end.isoformat())


async def async_setup_entry(hass, entry):
    """Set up NILM from a config entry."""
    host                = entry.data[CONF_HOST]
    port                = entry.data[CONF_PORT]
    power_entity        = entry.data[CONF_POWER_ENTITY]
    scan_interval       = entry.data[CONF_SCAN_INTERVAL]
    model_name          = entry.data[CONF_MODEL_NAME]
    freq_sample         = entry.data[CONF_SAMPLING_RATE]
    window_size         = entry.data[CONF_WINDOW_SIZE]
    enabled_appliances  = entry.data[CONF_ENABLED_APPLIANCES]
    data_push_interval  = entry.data.get(CONF_DATA_PUSH_INTERVAL, 24)
    base_url            = f"http://{host}:{port}"
    backend             = NilmBackend(base_url)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].update({
        "base_url":                 base_url,
        "backend":                  backend,
        "power_entity":             power_entity,
        "entry_id":                 entry.entry_id,
        "window_size":              window_size,
        "freq_sample":              freq_sample, # how often to sample the power entity (here 5s)
        "scan_interval":            scan_interval, # how often to send the buffer to the backend
        "raw_events":               [],  # (ts, value) tuples filled by the state listener
        "enabled_appliances":       enabled_appliances,
        "data_push_interval":       data_push_interval, # how often to push the history to the backend ( hour)
    })



    await backend.load_model(model_name)

    coordinator = NilmCoordinator(hass)
    hass.data[DOMAIN]["coordinator"] = coordinator
    
    # clean up when the custom component is unloaded
    entry.async_on_unload(coordinator.async_add_listener(lambda: None))

    # Register the hub device befores platform setup so via_device references resolve
    device_registry.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="NILM Hub",
        manufacturer="NILM",
        model="Energy Monitor",
    )

    await prefill_buffer_from_history(hass)
    _register_power_listener(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    register_services(hass)
    await setup_frontend(hass)

    for view in (
        StatusView(),
        AppliancesView(),
        DevicesView(),
        FinetuneView(),
        FinetuneResetView(),
        TrainView(),
        TrainStatusView(),
        ApplianceAddView(),
        ApplianceParamsView(),
        ApplianceRemoveView(),
        SetParametersView(),
        PushDataView(),
        ResetDataView(),
        CalibrateView(),
        CalibrateDeleteView(),
        LinkDeviceView(),
        UnlinkDeviceView(),
    ):
        hass.http.register_view(view)

    # Periodic history push to the backend every data_push_interval hours
    async def scheduled_push(_now=None) -> None:
        _LOGGER.debug(f"NILM: scheduled history push (every {data_push_interval}h)")
        await push_recent_history(hass, data_push_interval)

    # Register the periodic history push to the backend every data_push_interval hours
    cancel_push = async_track_time_interval(hass, scheduled_push, timedelta(hours=data_push_interval))
    entry.async_on_unload(cancel_push)

    _LOGGER.info(f"NILM: Setup complete (model={model_name}, window={window_size}, freq={freq_sample}s, history_push={data_push_interval}h)")
    return True


async def async_unload_entry(hass, entry):
    """Unload a NILM config entry."""
    hass.services.async_remove(DOMAIN, "push_data")

    async_remove_panel(hass, "nilm")

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.pop(DOMAIN, None)
    return unloaded

async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up persistent data when the config entry is deleted."""
    path = labels_path(hass)
    if os.path.exists(path):
        try:
            os.remove(path)
            _LOGGER.info("NILM: Removed user labels file on config entry deletion")
        except OSError as exc:
            _LOGGER.warning(f"NILM: Could not remove {path}. Details: {exc}")





class NilmCoordinator(DataUpdateCoordinator):
    """Coordinator that fills the rolling power buffer and runs /prediction every scan_interval."""

    def __init__(self, hass) -> None:
        scan_interval = hass.data[DOMAIN]["scan_interval"]
        super().__init__(
            hass,
            _LOGGER,
            name="NILM",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        """Resample the raw-event buffer onto a uniform grid and call /prediction."""

        data         = self.hass.data[DOMAIN]
        backend      = data["backend"]
        window_size  = data["window_size"]
        freq_sample  = data["freq_sample"]
        power_entity = data.get("power_entity")


        if power_entity:
            events  = data.setdefault("raw_events", [])
            now_ts  = dt_util.utcnow().timestamp()
            last_ts = events[-1][0] if events else None
            if last_ts is None or (now_ts - last_ts) >= freq_sample:
                state = self.hass.states.get(power_entity)
                if state is not None and state.state not in ("unavailable", "unknown", None, ""):
                    try:
                        events.append((now_ts, float(state.state)))
                        cutoff = now_ts - window_size * freq_sample
                        if events[0][0] < cutoff:
                            data["raw_events"] = [event for event in events if event[0] >= cutoff]
                    except (ValueError, TypeError):
                        pass

        events = list(data.get("raw_events") or [])
        if not events:
            _LOGGER.debug("NILM: No raw events in buffer yet, skipping prediction")
            return {}

        timestamps = [timestamp for timestamp, _ in events]
        values     = [value for _, value in events]
        buffer     = resample_to_grid(timestamps, values, window_size, freq_sample) or []

        if len(buffer) < window_size:
            _LOGGER.debug(f"NILM: Buffer too short ({len(buffer)}/{window_size}), skipping prediction")
            return {}

        try:
            status, body = await backend.predict(buffer)
            check_predict_status(status)

            predictions = normalize_predictions(body.get("appliances", {}))
            enabled     = data.get("enabled_appliances", [])
            if enabled:
                predictions = {appliance: prediction for appliance, prediction in predictions.items() if appliance in enabled}
            return predictions

        except UpdateFailed:
            raise
        except aiohttp.ClientError as exc:
            raise UpdateFailed(f"Connection failed: {exc}") from exc
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error: {exc}") from exc


def check_predict_status(status):
    """Raise UpdateFailed when the backend returned a non-200 status.

    The status -> message mapping lives in helpers.predict_status_message so
    it can be unit-tested without importing Home Assistant.
    """
    msg = predict_status_message(status)
    if msg is not None:
        raise UpdateFailed(msg)






# Service for automations and scripts. In fact, there is only data push for now, 
# because i thought it would be useful to have a manual trigger for pushing data to the backend, 
# in case the user wants to do it outside of the regular schedule 
# The service also accepts an optional hours parameter to specify how much history to push, 
# defaulting to the data_push_interval config option. 
def register_services(hass):

    async def handle_push_data(call: ServiceCall) -> None:
        """Push recent sensor history to the model server's /data endpoint."""
        hours = float(call.data.get("hours", hass.data[DOMAIN].get("data_push_interval", 24)))
        await push_recent_history(hass, hours)

    hass.services.async_register(DOMAIN, "push_data", handle_push_data, schema=vol.Schema({vol.Optional("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=8760)),}))



# ok
async def setup_frontend(hass):
    # Register the custom panel for NILM management UI
    js_url = f"/local/nilm-panel-v{COMPONENT_VERSION}.js"
    js_path = os.path.join(os.path.dirname(__file__), "frontend", "nilm-panel.js")

    await hass.http.async_register_static_paths([
        StaticPathConfig(js_url, js_path, cache_headers=False)
    ])

    await async_register_custom_panel(
        hass,
        frontend_url_path="nilm",
        webcomponent_name="nilm-management-panel",
        sidebar_title="NILM",
        sidebar_icon="mdi:lightning-bolt",
        js_url=js_url,
        require_admin=False,
    )
