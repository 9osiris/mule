"""plugin tools: ~/.mule/plugins/*.py exposing get_tools(),
plus json-defined external tools that run shell commands."""
import importlib.util
import json
import os
import shlex
import subprocess

MAX_EXTERNAL_OUTPUT = 20000


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


def _external_command_ok(command, root):
    # the command itself must live inside the project root when
    # it names a path. bare commands (echo, git) are fine, but
    # /tmp/evil.sh or ../evil.sh is refused outright.
    try:
        parts = shlex.split(command or "")
    except ValueError:
        return False
    if not parts:
        return False
    first = parts[0]
    if "/" not in first and "\\" not in first:
        return True
    full = first if os.path.isabs(first) else os.path.join(root, first)
    return os.path.commonpath([os.path.abspath(full), root]) == root


def _external_run(root, command, timeout, params, args):
    # substitute {arg} placeholders, shell-quoted, then run
    # inside the root with a timeout, like run_shell
    if not _external_command_ok(command, root):
        return ("error: external tool command escapes the project "
                "root, refused")
    cmd = command
    for name in params:
        cmd = cmd.replace("{%s}" % name,
                          shlex.quote(str(args.get(name, ""))))
    try:
        proc = subprocess.run(cmd, shell=True, cwd=root,
                              capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return "error: external tool timed out after %ss" % timeout
    except OSError as e:
        return "error: external tool failed to start: %s" % e
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_EXTERNAL_OUTPUT:
        out = out[:MAX_EXTERNAL_OUTPUT] + "\n...[truncated]"
    return "exit %d\n%s" % (proc.returncode, out.rstrip() or "(no output)")


def _make_external_runner(root, command, timeout, params):
    def _run_tool(**args):
        return _external_run(root, command, timeout, params, args)
    return _run_tool


def load_external_tools(path, root):
    # json file: {"tools": [{name, description, command,
    # parameters, timeout}]}. a top-level list works too.
    # returns ([tool dicts], [error strings]).
    root = os.path.abspath(root)
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return [], ["external tools file %s: %s" % (path, e)]
    raw = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return [], ["external tools file %s: expected a list of tools"
                    % path]
    tools, errors = [], []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            errors.append("external tool %d is not an object" % i)
            continue
        name, command = t.get("name", ""), t.get("command", "")
        if not name or not command:
            errors.append("external tool %d needs a name and a command"
                          % i)
            continue
        params = t.get("parameters") or t.get("args") or {}
        if isinstance(params, list):
            params = {str(p): "" for p in params}
        if not isinstance(params, dict):
            errors.append("external tool %r parameters must be an object"
                          % name)
            continue
        try:
            timeout = float(t.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30
        timeout = min(max(timeout, 1), 300)
        params = {str(k): str(v) for k, v in params.items()}
        tools.append({
            "name": str(name),
            "description": str(t.get("description") or name),
            "parameters": params,
            "run": _make_external_runner(root, command, timeout, params),
        })
    return tools, errors
