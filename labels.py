"""User label persistence for the NILM component."""
from __future__ import annotations

import json
import logging
import os

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def labels_path(hass: HomeAssistant) -> str:
    storage_dir = hass.config.path(".storage")
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, "nilm_user_labels.json")


def load_labels(hass: HomeAssistant) -> dict:
    path = labels_path(hass)
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception as exc:
            _LOGGER.warning(f"NILM: could not read user_labels.json. Details: {exc}")
    return {}


def save_labels(hass: HomeAssistant, labels: dict) -> None:
    path = labels_path(hass)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(labels, fh, indent=2)


async def async_load_labels(hass: HomeAssistant) -> dict:
    return await hass.async_add_executor_job(load_labels, hass)


async def async_save_labels(hass: HomeAssistant, labels: dict) -> None:
    await hass.async_add_executor_job(save_labels, hass, labels)
