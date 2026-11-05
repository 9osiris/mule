"""named profiles: ~/.mule/profiles/NAME.json, plus env expansion."""
import json
import os
import re

try:
    from config import KEYS as _KEYS
except Exception:
    _KEYS = ("model", "base_url", "api_key", "max_steps", "timeout", "root",
             "retries", "context_budget", "ask", "reflect", "verbose",
             "parallel_tools", "readonly", "no_network", "allow_tools",
             "deny_tools", "fallback_model", "fallback_models", "max_tools",
             "time_limit", "temperature", "max_tool_output", "allow_cmds",
             "deny_cmds", "external_tools", "cache")

_ENV_RE = re.compile(r"\$(\{([^}]+)\}|([A-Za-z_][A-Za-z0-9_]*))")


def profile_dir(profiles_dir=None):
    # explicit dir wins (tests), else the real ~/.mule/profiles
    if profiles_dir:
        d = os.path.abspath(profiles_dir)
    else:
        d = os.path.join(os.path.expanduser("~"), ".mule", "profiles")
    os.makedirs(d, exist_ok=True)
    return d


def list_profiles(profiles_dir=None):
    d = profile_dir(profiles_dir)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(f[:-5] for f in names if f.endswith(".json"))


def load_profile(name, profiles_dir=None):
    # read one profile, filtered to known config keys
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("no such profile: %s" % name)
    path = os.path.join(profile_dir(profiles_dir), name + ".json")
    if not os.path.isfile(path):
        avail = list_profiles(profiles_dir)
        hint = ", ".join(avail) if avail else "(none yet)"
        raise ValueError("no such profile: %s. available: %s"
                         % (name, hint))
    try:
        with open(path) as f:
            data = json.load(f)
    except ValueError:
        raise ValueError("profile %s is not valid json" % name)
    except OSError as e:
        raise ValueError("profile %s cannot be read: %s" % (name, e))
    if not isinstance(data, dict):
        raise ValueError("profile %s must be a json object" % name)
    return {k: data[k] for k in _KEYS if k in data}


def apply_profile(cfg, name, overrides=None, profiles_dir=None):
    # profile fills gaps, explicit values win: profile under overrides
    profile = load_profile(name, profiles_dir)
    merged = dict(profile)
    if cfg:
        merged.update(cfg)
    if overrides:
        merged.update(overrides)
    return merged


def _expand_str(text):
    # $VAR and ${VAR} expand, unknown vars stay as written
    def _sub(match):
        var = match.group(2) or match.group(3)
        return os.environ.get(var, match.group(0))
    return _ENV_RE.sub(_sub, text)


def expand_env_vars(obj):
    # recursively expand env vars in strings inside dicts and lists
    if isinstance(obj, str):
        return _expand_str(obj)
    if isinstance(obj, dict):
        return {_expand_str(k) if isinstance(k, str) else k:
                expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(expand_env_vars(v) for v in obj)
    return obj
