"""mule doctor: sanity-check the setup and report what is broken."""
import json
import os
import urllib.request


def _check_config(path, label):
    # missing file is fine (defaults apply), broken json is not
    if not os.path.exists(path):
        return True, "%s not present, using defaults" % label
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, "%s is not valid json" % label
    if not isinstance(data, dict):
        return False, "%s must be a json object" % label
    return True, "%s parses" % label


def _check_key():
    # never print the key itself, just whether one is configured
    from config import load_config
    if os.environ.get("OPENAI_API_KEY") or load_config().get("api_key"):
        return True, "api key found (env or config)"
    return False, ("no api key: set OPENAI_API_KEY or run "
                   "mule config set api_key <key>")


def _check_reachable():
    # any http response counts, even a 401. silence means unreachable.
    from config import load_config
    cfg = load_config()
    base = os.environ.get("MULE_BASE_URL", cfg.get("base_url",
                                                   "https://api.openai.com/v1"))
    url = base.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=5)
        return True, "reached %s" % base
    except Exception as e:
        if hasattr(e, "code"):
            # the server answered (e.g. 401 without a key), that is fine
            return True, "reached %s (http %s)" % (base, e.code)
        return False, "cannot reach %s: %s" % (base, e)


def _check_root(root):
    if os.path.isdir(root):
        return True, "project root exists: %s" % os.path.abspath(root)
    return False, "project root missing: %s" % root


def run_doctor(root="."):
    """print a checklist, return 0 when everything passes."""
    from config import HOME_CONFIG, LOCAL_CONFIG
    checks = [
        _check_config(LOCAL_CONFIG, "local mule.json"),
        _check_config(HOME_CONFIG, "global mule.json"),
        _check_key(),
        _check_reachable(),
        _check_root(root),
    ]
    ok = True
    for passed, label in checks:
        ok = ok and passed
        print("[%s] %s" % ("ok" if passed else "fail", label))
    return 0 if ok else 1
