"""plugin tools: ~/.mule/plugins/*.py exposing get_tools()."""
import importlib.util
import os


def plugin_dir():
    d = os.path.join(os.path.expanduser("~"), ".mule", "plugins")
    os.makedirs(d, exist_ok=True)
    return d


def _load_one(name, path):
    # import the file, call get_tools(), validate the shape
    spec = importlib.util.spec_from_file_location(
        "mule_plugin_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    get_tools = getattr(mod, "get_tools", None)
    if not callable(get_tools):
        raise ValueError("no get_tools() function")
    raw = get_tools()
    if not isinstance(raw, list):
        raise ValueError("get_tools() must return a list")
    out = []
    for t in raw:
        if not isinstance(t, dict):
            raise ValueError("each tool must be a dict")
        for key in ("name", "description", "handler"):
            if key not in t:
                raise ValueError("tool missing %r" % key)
        if not callable(t["handler"]):
            raise ValueError("tool %r handler is not callable"
                             % (t.get("name"),))
        params = t.get("parameters") or {}
        if not isinstance(params, dict):
            raise ValueError("tool %r parameters must be a dict"
                             % (t.get("name"),))
        out.append({
            "name": str(t["name"]),
            "description": str(t["description"]),
            "parameters": {k: str(v) for k, v in params.items()},
            "run": t["handler"],
        })
    return out


def load_plugins():
    # returns ([tool dicts], [error strings]). a bad plugin never
    # takes down startup, it just shows up in errors.
    tools, errors = [], []
    d = plugin_dir()
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        name = fname[:-3]
        try:
            tools.extend(_load_one(name, os.path.join(d, fname)))
        except Exception as e:
            errors.append("plugin %s failed to load: %s" % (name, e))
    return tools, errors
