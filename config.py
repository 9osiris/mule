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


def _write(path, data):
    # write the known keys back, sorted, for a stable file
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w") as f:
        json.dump({k: data[k] for k in KEYS if k in data},
                  f, indent=2, sort_keys=True)
        f.write("\n")


def load_config():
    # home config first, local mule.json overrides it
    cfg = _read(HOME_CONFIG)
    cfg.update(_read(LOCAL_CONFIG))
    return cfg


def config_path(global_=False):
    return HOME_CONFIG if global_ else LOCAL_CONFIG


def config_list(global_=False):
    return _read(config_path(global_))


def config_get(key, global_=False):
    if key not in KEYS:
        raise KeyError("unknown config key: %s (try: %s)"
                       % (key, ", ".join(KEYS)))
    return config_list(global_).get(key)


def config_set(key, value, global_=False):
    # "40" becomes 40, "true" becomes True, the rest stays a string
    if key not in KEYS:
        raise KeyError("unknown config key: %s (try: %s)"
                       % (key, ", ".join(KEYS)))
    try:
        value = json.loads(value)
    except (ValueError, TypeError):
        pass
    data = config_list(global_)
    data[key] = value
    _write(config_path(global_), data)
    return value


def config_unset(key, global_=False):
    if key not in KEYS:
        raise KeyError("unknown config key: %s (try: %s)"
                       % (key, ", ".join(KEYS)))
    data = config_list(global_)
    data.pop(key, None)
    _write(config_path(global_), data)
