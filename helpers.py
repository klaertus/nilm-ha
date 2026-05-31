"""Funcs shared by the NILM component.

"""
from __future__ import annotations

import re
from typing import Optional
import logging
from datetime import datetime, timezone

import numpy as np

_LOGGER = logging.getLogger(__name__)


def normalize_predictions(raw):
    """Convert raw power floats {appliance: watts} to {appliance: {power, state}}.

    The backend already zeroes the power of OFF appliances (its classifier
    applies on_probability_threshold), so state is simply power > 0 (user friendly).
    """
    return {
        appliance_id: {
            "power": power,
            "state": 1 if power > 0 else 0,
        }
        for appliance_id, power in raw.items()
    }



def parse_ha_states(entity_states: list[dict | State]) -> tuple[list[float], list[float]]:
    """Extract (timestamps_epoch_s, values) from a HA recorder state list.

    Returns:
        timestamps_list : list of epoch timestamps in seconds (float)
        values_list : list of power values (float), parallel to timestamps_list
    """
    timestamps_list: list[float] = []
    values_list: list[float] = []

    for state in entity_states:
        if isinstance(state, dict):
            raw_value = state.get("state")
            raw_timestamp = state.get("last_changed")
        else:
            raw_value = state.state
            raw_timestamp = state.last_changed

        if raw_value in ("unavailable", "unknown", None, ""):
            continue
        try:
            values_list.append(float(raw_value))
        except (ValueError, TypeError):
            continue

        # Convert timestamp to epoch seconds. Drop the sample if conversion
        # fails
        try:
            if hasattr(raw_timestamp, "timestamp"):
                timestamps_list.append(raw_timestamp.timestamp())
            else:
                timestamps_list.append(
                    datetime.fromisoformat(str(raw_timestamp))
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
        except Exception:
            values_list.pop()
            continue

    return timestamps_list, values_list


def resample_to_grid(timestamps_list: list[float], values_list: list[float], window_size: int, freq_sample: int) -> list[float] | None:
    """Resample irregular time series data onto a uniform grid.

    Keeps only the most recent (window_size * freq_sample) seconds of data,
    then zero order hold resamples onto a uniform grid of exactly window_size points.

    Args:
        timestamps_list : list of epoch timestamps in seconds
        values_list : list of power values, parallel to timestamps_list
        window_size : number of output points (e.g. PREDICT_WINDOW_SIZE)
        freq_sample : target period in seconds (e.g. FREQ_SAMPLE)

    Returns:
        List of window_size floats, or None if input is empty.
    """
    if not values_list:
        return None

    timestamps_arr = np.array(timestamps_list, dtype=float)
    values_arr = np.array(values_list, dtype=float)

    # Sort by time (recorder usually delivers ordered data, but be safe)
    order = np.argsort(timestamps_arr)
    timestamps_arr = timestamps_arr[order]
    values_arr = values_arr[order]

    # Trim to the most recent window_size * freq_sample seconds
    window_duration = window_size * freq_sample
    end_timestamp = timestamps_arr[-1]
    mask = timestamps_arr >= (end_timestamp - window_duration)
    timestamps_arr = timestamps_arr[mask]
    values_arr = values_arr[mask]

    if len(timestamps_arr) < 2:
        # Not enough points to forward fill, so return constant fill
        return [float(values_arr[0])] * window_size

    # Build uniform time grid and zero order hold (forward fill)
    time_grid = np.linspace(timestamps_arr[0], timestamps_arr[-1], window_size)
    indices = np.searchsorted(timestamps_arr, time_grid, side='right') - 1
    indices = np.clip(indices, 0, len(values_arr) - 1)
    buffer = values_arr[indices].tolist()

    _LOGGER.debug(f"NILM resample: {len(values_arr)} -> {window_size} points over {round(timestamps_arr[-1] - timestamps_arr[0])}s (target period {freq_sample}s)")
    return buffer



def is_safe_name(name: object) -> bool:
    """Return True if name is a valid model/appliance identifier."""
    safe = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
    return isinstance(name, str) and safe.match(name) is not None


def predict_status_message(status: int) -> Optional[str]:
    """Map a backend HTTP status to a human message.

    Returns None when the status is success (200), so the caller knows there
    is nothing to raise. The status codes match the backend exception
    hierarchy in app/lib/exceptions.py.
    """
    if status == 200:
        return None
    if status == 400:
        return "Invalid prediction payload (400) - check window size"
    if status == 404:
        return "No model loaded on backend (404)"
    if status == 409:
        return "Backend busy or no model loaded (409)"
    if status == 422:
        return "Pydantic validation rejected the payload (422)"
    return f"POST /prediction returned HTTP {status}"


def compute_grid(start_ts: float, end_ts: float, freq_sample: int) -> list[float]:
    """Construct a list of timestamps from start to end, spaced by freq_sample seconds.

    Args:
        start_ts: Starting timestamp (seconds since epoch).
        end_ts: Ending timestamp (seconds since epoch).
        freq_sample: Desired spacing between timestamps in seconds.

    Returns:
        List of timestamps from start_ts to end_ts, spaced by freq_sample seconds.
    """
    if end_ts < start_ts:
        raise ValueError(f"end_ts ({end_ts}) must be >= start_ts ({start_ts})")
    step = max(int(freq_sample), 1)
    span = end_ts - start_ts
    n_points = max(2, int(span / step) + 1)
    return [start_ts + i * step for i in range(n_points)]
