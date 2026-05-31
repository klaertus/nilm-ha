"""History fetch and data push for the NILM component."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .backend import NilmBackend
from .const import DOMAIN
from .helpers import compute_grid
from .labels import async_load_labels
from .helpers import parse_ha_states, resample_to_grid
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.util.dt import as_utc, parse_datetime

_LOGGER = logging.getLogger(__name__)


def get_backend(hass: HomeAssistant) -> NilmBackend | None:
    """Return the shared NilmBackend instance, or None if not set up yet."""
    return hass.data.get(DOMAIN, {}).get("backend")


async def async_send_data(hass: HomeAssistant, start_iso: str | None = None, end_iso: str | None = None) -> dict:
    """Fetch HA recorder history for all linked appliances and POST to /data.

    start_iso / end_iso must be ISO-8601 strings defining the window.
    Returns the response body from the model server, or an error dict.
    """
    labels = await async_load_labels(hass)
    linked: dict[str, str] = {
        appliance_id: meta["linked_entity"]
        for appliance_id, meta in labels.items()
        if meta.get("linked_entity")
    }

    power_entity: str | None = hass.data.get(DOMAIN, {}).get("power_entity")

    if not (start_iso and end_iso):
        _LOGGER.warning("NILM: push_data : no start/end ISO provided")
        return {"skipped": True, "reason": "no start/end ISO provided"}

    try:
        
        parsed_start = parse_datetime(start_iso)
        parsed_end   = parse_datetime(end_iso)
        start = as_utc(parsed_start) if parsed_start else dt_util.utcnow() - timedelta(hours=1)
        end   = as_utc(parsed_end)   if parsed_end   else dt_util.utcnow()
    except Exception as exc:
        _LOGGER.warning(f"NILM: push_data : could not parse start/end ISO. Details: {exc}")
        return {"skipped": True, "reason": "could not parse start/end ISO"}

    entities_to_fetch = list(set(linked.values()))
    if power_entity:
        entities_to_fetch.append(power_entity)

    _LOGGER.debug(f"NILM: push_data : querying entities={entities_to_fetch}  start={start.isoformat()}  end={end.isoformat()}")

    try:


        states : dict = await get_instance(hass).async_add_executor_job(
            lambda: get_significant_states(
                hass, start, end, entities_to_fetch,
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
            )
        )

    
    except Exception as exc:
        _LOGGER.error(f"NILM: push_data : could not read recorder history. Details: {exc}")
        return {"error": str(exc)}

    _LOGGER.debug(f"NILM: push_data : recorder returned: {len(states)} entities")

    freq_sample: int = hass.data.get(DOMAIN, {}).get("freq_sample", 5)

    # Resample every series onto the SAME uniform grid on the
    # aggregate power series
    if power_entity:
        aggregate_timestamps, aggregate_values = parse_ha_states(states.get(power_entity, []))
    else:
        aggregate_timestamps, aggregate_values = [], []

    if not aggregate_timestamps:
        _LOGGER.debug("NILM: push_data : aggregate series empty, nothing to send")
        return {"skipped": True, "reason": "aggregate series empty"}

    grid = compute_grid(aggregate_timestamps[0], aggregate_timestamps[-1], freq_sample)
    n_points = len(grid)

    def to_series_on_grid(entity_id: str) -> list:
        timestamps, values = parse_ha_states(states.get(entity_id, []))
        resampled = resample_to_grid(timestamps_list=timestamps, values_list=values, window_size=n_points, freq_sample=freq_sample)
        if not resampled:
            return []
        return [[grid[index], value] for index, value in enumerate(resampled)]

    total = to_series_on_grid(power_entity) if power_entity else []
    appliances = {
        appliance_id: series
        for appliance_id, entity_id in linked.items()
        if (series := to_series_on_grid(entity_id))
    }

    _LOGGER.debug(f"NILM: push_data : built series: total={len(total)} pts, appliances={len(appliances)} entities")

    if not total and not appliances:
        _LOGGER.debug(f"NILM: push_data : no history found in window")
        return {"skipped": True, "reason": "no history found"}

    _LOGGER.info(f"NILM: push_data : sending {len(total)} total points, {len(appliances)} appliances")

    backend = get_backend(hass)
    if not backend:
        return {"error": "backend not configured"}

    try:
        _, body = await backend.add_data(total, appliances)
        _LOGGER.info(f"NILM: /data : {body}")
        return body
    except Exception as exc:
        _LOGGER.error(f"NILM: /data failed. Details: {exc}")
        return {"error": str(exc)}
