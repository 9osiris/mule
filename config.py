"""defaults from mule.json: ./mule.json wins over ~/.config/mule/mule.json."""
import json
import os

KEYS = ("model", "base_url", "api_key", "max_steps", "timeout", "root",
        "retries", "context_budget", "ask", "reflect", "verbose",
        "parallel_tools", "readonly", "no_network", "allow_tools",
        "deny_tools", "fallback_model", "max_tools", "time_limit",
        "temperature")
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


def _read_raw(path):
    # the unfiltered file, for validation
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


SCHEMA = {
    "model": str, "base_url": str, "api_key": str, "root": str,
    "max_steps": int, "timeout": (int, float), "retries": int,
    "context_budget": int, "ask": bool, "reflect": bool,
    "verbose": bool, "parallel_tools": bool, "readonly": bool,
    "no_network": bool, "allow_tools": str, "deny_tools": str,
    "fallback_model": str, "max_tools": int,
    "time_limit": (int, float), "temperature": (int, float),
}


def _want_name(want):
    if want is str:
        return "a string"
    if want is int:
        return "an integer"
    if want is bool:
        return "true or false"
    return "a number"


def validate_config(data, source="config"):
    # human-readable problems: unknown keys, wrong value types
    problems = []
    for key in sorted(data):
        want = SCHEMA.get(key)
        if want is None:
            problems.append(
                "unknown key %r in %s (known keys: %s)"
                % (key, source, ", ".join(sorted(SCHEMA))))
            continue
        value = data[key]
        ok = isinstance(value, want)
        if want is int and isinstance(value, bool):
            ok = False  # True is an int in python, not here
        if not ok:
            problems.append(
                "key %r in %s should be %s, got %s"
                % (key, source, _want_name(want),
                   type(value).__name__))
    return problems


def config_problems():
    # validate both config files, naming the file in each problem
    problems = []
    for path in (HOME_CONFIG, LOCAL_CONFIG):
        raw = _read_raw(path)
        if raw:
            problems.extend(validate_config(raw, source=path))
    return problems


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


def profile_dir():
    d = os.path.join(os.path.expanduser("~"), ".mule", "profiles")
    os.makedirs(d, exist_ok=True)
    return d


def list_profiles():
    d = profile_dir()
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def load_profile(name):
    # ~/.mule/profiles/NAME.json, merged over config, under cli flags
    path = os.path.join(profile_dir(), name + ".json")
    if not os.path.isfile(path):
        avail = list_profiles()
        hint = ", ".join(avail) if avail else "(none yet)"
        raise ValueError("no such profile: %s. available: %s" % (name, hint))
    try:
        with open(path) as f:
            data = json.load(f)
    except ValueError:
        raise ValueError("profile %s is not valid json" % name)
    if not isinstance(data, dict):
        raise ValueError("profile %s must be a json object" % name)
    return {k: data[k] for k in KEYS if k in data}
