"""defaults from mule.json: ./mule.json wins over ~/.config/mule/mule.json."""
import json
import os

KEYS = ("model", "base_url", "api_key", "max_steps", "timeout", "root",
        "retries", "context_budget")
HOME_CONFIG = os.path.expanduser("~/.config/mule/mule.json")
LOCAL_CONFIG = "mule.json"


def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in KEYS if k in data}


def load_config():
    # home config first, local mule.json overrides it
    cfg = _read(HOME_CONFIG)
    cfg.update(_read(LOCAL_CONFIG))
    return cfg
