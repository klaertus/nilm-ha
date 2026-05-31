"""Constants for the NILM integration."""
import json
import os

DOMAIN = "nilm"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_POWER_ENTITY = "power_entity_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_MODEL_NAME = "model_name"
CONF_ENABLED_APPLIANCES = "enabled_appliances"
CONF_DATA_PUSH_INTERVAL = "data_push_interval"  # hours between automatic data pushes


with open(os.path.join(os.path.dirname(__file__), "manifest.json"), encoding="utf-8") as manifest_file:
    COMPONENT_VERSION = json.load(manifest_file)["version"]

# NILM prediction window 
CONF_SAMPLING_RATE = "sampling_rate" # key stored in entry.data
CONF_WINDOW_SIZE = "window_size" # key stored in entry.data


