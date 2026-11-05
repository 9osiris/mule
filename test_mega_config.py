"""tests for profiles, prompt files, plugin reload, doctor checks,
and scaffold templates. no api key needed, temp dirs only."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


# profiles

from profiles import (load_profile, list_profiles, apply_profile,
                      expand_env_vars)

_pdir = tempfile.mkdtemp()
with open(os.path.join(_pdir, "work.json"), "w") as f:
    json.dump({"model": "gpt-4o", "max_steps": 5, "nope_key": 1}, f)
with open(os.path.join(_pdir, "bad.json"), "w") as f:
    f.write("{not json")
with open(os.path.join(_pdir, "list.json"), "w") as f:
    f.write("[1, 2]")

check("load_profile reads the file",
      load_profile("work", _pdir) == {"model": "gpt-4o", "max_steps": 5})
check("load_profile drops unknown keys",
      "nope_key" not in load_profile("work", _pdir))
check("list_profiles finds them",
      list_profiles(_pdir) == ["bad", "list", "work"])
try:
    load_profile("nope", _pdir)
    _pok = False
except ValueError as e:
    _pok = "available: bad, list, work" in str(e)
check("missing profile errors with available list", _pok)
try:
    load_profile("bad", _pdir)
    _bok = False
except ValueError as e:
    _bok = "not valid json" in str(e)
check("bad json profile errors cleanly", _bok)
try:
    load_profile("list", _pdir)
    _lok = False
except ValueError as e:
    _lok = "must be a json object" in str(e)
check("non-object profile errors cleanly", _lok)
check("list_profiles on a missing dir is empty",
      list_profiles(os.path.join(tempfile.mkdtemp(), "nope")) == [])

check("apply_profile fills gaps from the profile",
      apply_profile({}, "work", profiles_dir=_pdir)
      == {"model": "gpt-4o", "max_steps": 5})
check("apply_profile lets explicit cfg beat the profile",
      apply_profile({"model": "mini"}, "work", profiles_dir=_pdir)
      == {"model": "mini", "max_steps": 5})
check("apply_profile lets overrides beat cfg and profile",
      apply_profile({"model": "mini"}, "work",
                    overrides={"model": "big", "max_steps": 9},
                    profiles_dir=_pdir)
      == {"model": "big", "max_steps": 9})
check("apply_profile returns a new dict",
      apply_profile({"model": "mini"}, "work", profiles_dir=_pdir)
      is not load_profile("work", _pdir))

os.environ["MULE_TEST_HOME"] = "/tmp/home"
os.environ["MULE_TEST_MODEL"] = "gpt-4o"
check("expand_env_vars expands $VAR",
      expand_env_vars({"root": "$MULE_TEST_HOME"})["root"] == "/tmp/home")
check("expand_env_vars expands ${VAR}",
      expand_env_vars({"model": "${MULE_TEST_MODEL}"})["model"] == "gpt-4o")
check("expand_env_vars leaves unknown vars untouched",
      expand_env_vars("$MULE_TEST_NOPE/x") == "$MULE_TEST_NOPE/x")
check("expand_env_vars leaves ${UNKNOWN} untouched",
      expand_env_vars("${MULE_TEST_NOPE}") == "${MULE_TEST_NOPE}")
check("expand_env_vars recurses into lists and nested dicts",
      expand_env_vars({"a": ["$MULE_TEST_MODEL", {"b": "$MULE_TEST_HOME"}]})
      == {"a": ["gpt-4o", {"b": "/tmp/home"}]})
check("expand_env_vars passes non-strings through",
      expand_env_vars({"n": 3, "ok": True, "x": None})
      == {"n": 3, "ok": True, "x": None})
os.environ.pop("MULE_TEST_HOME", None)
os.environ.pop("MULE_TEST_MODEL", None)

# prompt files

from promptfiles import load_system_file, render_template, list_templates

_tdir = tempfile.mkdtemp()
_spath = os.path.join(_tdir, "sys.md")
with open(_spath, "w") as f:
    f.write("\n  be helpful.\n\n")
with open(os.path.join(_tdir, "a.md"), "w") as f:
    f.write("a")
with open(os.path.join(_tdir, "b.txt"), "w") as f:
    f.write("b")

check("load_system_file reads and strips",
      load_system_file(_spath) == "be helpful.")
try:
    load_system_file(os.path.join(_tdir, "missing.md"))
    _sok = False
except ValueError:
    _sok = True
check("load_system_file errors on a missing file", _sok)
check("render_template replaces a placeholder",
      render_template("hi {{name}}", {"name": "osiris"}) == "hi osiris")
check("render_template allows spaces in braces",
      render_template("hi {{ name }}", {"name": "osiris"}) == "hi osiris")
check("render_template leaves unknown placeholders alone",
      render_template("hi {{name}} {{other}}", {"name": "x"})
      == "hi x {{other}}")
check("render_template stringifies values",
      render_template("steps {{n}}", {"n": 5}) == "steps 5")
check("render_template with no vars returns the text",
      render_template("plain text") == "plain text")
check("list_templates lists files in the dir",
      list_templates(_tdir) == ["a.md", "b.txt", "sys.md"])
check("list_templates on a missing dir is empty",
      list_templates(os.path.join(_tdir, "nope")) == [])

# plugin hot reload

from plugins import load_plugins, reload_plugin, plugin_meta

_old_home = os.environ.get("HOME")
_phome = tempfile.mkdtemp()
os.environ["HOME"] = _phome
_plugdir = os.path.join(_phome, ".mule", "plugins")
os.makedirs(_plugdir)
with open(os.path.join(_plugdir, "shout.py"), "w") as f:
    f.write("def get_tools():\n"
            "    return [{'name': 'shout',\n"
            "             'description': 'yell text',\n"
            "             'parameters': {'text': 'what to yell'},\n"
            "             'handler': lambda text='': text.upper() + '!'}]\n")
_tools, _errs = load_plugins()
check("plugin loads before reload", len(_tools) == 1 and not _errs)
_meta = plugin_meta("shout")
check("plugin_meta reports name and file",
      _meta["name"] == "shout" and _meta["file"].endswith("shout.py"))
check("plugin_meta reports the tool count",
      _meta["tool_count"] == 1)
check("plugin_meta reports a load time",
      _meta["loaded_at"] > 0 and _meta["loaded_time"] == _meta["loaded_at"])
try:
    plugin_meta("nope")
    _mok = False
except ValueError:
    _mok = True
check("plugin_meta errors on an unloaded plugin", _mok)

with open(os.path.join(_plugdir, "shout.py"), "w") as f:
    f.write("def get_tools():\n"
            "    return [{'name': 'shout',\n"
            "             'description': 'yell text',\n"
            "             'parameters': {'text': 'what to yell'},\n"
            "             'handler': lambda text='': text.upper() + '?'},\n"
            "            {'name': 'whisper',\n"
            "             'description': 'quiet text',\n"
            "             'parameters': {'text': 'what to say'},\n"
            "             'handler': lambda text='': text.lower()}]\n")
_reloaded = reload_plugin("shout")
check("reload_plugin re-reads the file",
      len(_reloaded) == 2
      and _reloaded[0]["run"](text="hey") == "HEY?")
check("reload_plugin replaces the registered tools",
      plugin_meta("shout")["tool_count"] == 2)
check("reloaded plugin tools run",
      _reloaded[1]["run"](text="HEY") == "hey")
try:
    reload_plugin("nope")
    _rok = False
except ValueError:
    _rok = True
check("reload_plugin errors on a missing plugin file", _rok)

with open(os.path.join(_plugdir, "fresh.py"), "w") as f:
    f.write("def get_tools():\n"
            "    return [{'name': 'fresh', 'description': 'new',\n"
            "             'parameters': {}, 'handler': lambda: 'ok'}]\n")
_fresh = reload_plugin("fresh")
check("reload_plugin loads a never-loaded plugin",
      len(_fresh) == 1 and plugin_meta("fresh")["tool_count"] == 1)
if _old_home is None:
    os.environ.pop("HOME", None)
else:
    os.environ["HOME"] = _old_home

# doctor: model connectivity and config validity

from doctor import (check_model_connectivity, check_config_validity,
                    run_doctor)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


_srv = HTTPServer(("127.0.0.1", 0), _OkHandler)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_live = "http://127.0.0.1:%d/v1" % _srv.server_port

ok, label = check_model_connectivity(_live, timeout=5)
check("connectivity check reaches a live base url", ok)
ok, label = check_model_connectivity(_live, timeout=5)
check("connectivity check counts a 404 as reachable", ok)
os.environ["OPENAI_API_KEY"] = "sk-secret-xyz"
ok, label = check_model_connectivity("http://127.0.0.1:1/v1", timeout=2)
check("connectivity check fails cleanly on a dead port", not ok)
check("connectivity check never leaks the api key",
      "sk-secret-xyz" not in label)
check("connectivity check is fast offline",
      check_model_connectivity("http://127.0.0.1:1/v1",
                               timeout=2)[0] is False)
os.environ.pop("OPENAI_API_KEY", None)
_srv.shutdown()

_cdir = tempfile.mkdtemp()
_good = os.path.join(_cdir, "good.json")
with open(_good, "w") as f:
    json.dump({"model": "gpt-4o", "max_steps": 5}, f)
_unknown = os.path.join(_cdir, "unknown.json")
with open(_unknown, "w") as f:
    json.dump({"model": "gpt-4o", "bogus_key": 1}, f)
_wrong = os.path.join(_cdir, "wrong.json")
with open(_wrong, "w") as f:
    json.dump({"max_steps": "five"}, f)
_broken = os.path.join(_cdir, "broken.json")
with open(_broken, "w") as f:
    f.write("not json{")

check("config validity passes a good config",
      check_config_validity([_good])[0])
check("config validity flags unknown keys",
      "bogus_key" in check_config_validity([_unknown])[1])
check("config validity flags wrong value types",
      "max_steps" in check_config_validity([_wrong])[1])
check("config validity flags broken json",
      not check_config_validity([_broken])[0])
check("config validity is fine with missing files",
      check_config_validity([os.path.join(_cdir, "nope.json")])[0])
check("config validity checks every file given",
      not check_config_validity([_good, _unknown])[0])

# doctor end to end, with temp config and a live base url

import config as config_mod

_srv2 = HTTPServer(("127.0.0.1", 0), _OkHandler)
threading.Thread(target=_srv2.serve_forever, daemon=True).start()
_doc_home = tempfile.mkdtemp()
_doc_orig = (config_mod.HOME_CONFIG, config_mod.LOCAL_CONFIG)
config_mod.HOME_CONFIG = os.path.join(_doc_home, "mule.json")
config_mod.LOCAL_CONFIG = os.path.join(_doc_home, "local.json")
_doc_env = dict(os.environ)
os.environ.pop("OPENAI_API_KEY", None)
os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ["MULE_BASE_URL"] = "http://127.0.0.1:%d/v1" % _srv2.server_port
check("doctor returns 0 when healthy with the new checks",
      run_doctor(_doc_home) == 0)
with open(config_mod.LOCAL_CONFIG, "w") as f:
    json.dump({"model": "gpt-4o", "bogus_key": 1}, f)
check("doctor returns 1 on an invalid config",
      run_doctor(_doc_home) == 1)
os.environ.clear()
os.environ.update(_doc_env)
config_mod.HOME_CONFIG, config_mod.LOCAL_CONFIG = _doc_orig
_srv2.shutdown()

# scaffold templates

from scaffold import list_templates, scaffold_project

check("list_templates has at least 3 templates",
      len(list_templates()) >= 3)
check("list_templates has minimal, python-lib, web-app",
      set(["minimal", "python-lib", "web-app"]) <= set(list_templates()))

for _tpl in list_templates():
    _dest = os.path.join(tempfile.mkdtemp(), "proj-" + _tpl)
    _made = scaffold_project(_tpl, _dest)
    check("scaffold %s creates mule.json" % _tpl,
          os.path.isfile(os.path.join(_dest, "mule.json")))
    check("scaffold %s creates a README stub" % _tpl,
          os.path.isfile(os.path.join(_dest, "README.md")))
    check("scaffold %s creates .gitignore" % _tpl,
          os.path.isfile(os.path.join(_dest, ".gitignore")))
    check("scaffold %s mule.json parses" % _tpl,
          json.load(open(os.path.join(_dest, "mule.json")))["model"]
          == "gpt-4o-mini")
    check("scaffold %s returns the created files" % _tpl,
          len(_made) >= 3)

_lib = os.path.join(tempfile.mkdtemp(), "mylib")
scaffold_project("python-lib", _lib)
check("python-lib scaffold has a package and tests",
      os.path.isfile(os.path.join(_lib, "src", "mylib", "__init__.py"))
      and os.path.isfile(os.path.join(_lib, "pyproject.toml"))
      and os.path.isfile(os.path.join(_lib, "tests", "test_mylib.py")))
_web = os.path.join(tempfile.mkdtemp(), "myweb")
scaffold_project("web-app", _web)
check("web-app scaffold has html and app entry",
      os.path.isfile(os.path.join(_web, "index.html"))
      and os.path.isfile(os.path.join(_web, "app.py")))

_busy = tempfile.mkdtemp()
with open(os.path.join(_busy, "keep.txt"), "w") as f:
    f.write("keep")
try:
    scaffold_project("minimal", _busy)
    _sok = False
except FileExistsError:
    _sok = True
check("scaffold refuses a non-empty dest", _sok)
check("scaffold leaves a refused dest untouched",
      open(os.path.join(_busy, "keep.txt")).read() == "keep")
try:
    scaffold_project("nope", os.path.join(tempfile.mkdtemp(), "x"))
    _uok = False
except ValueError as e:
    _uok = "minimal" in str(e)
check("scaffold errors on an unknown template", _uok)
_empty = tempfile.mkdtemp()
check("scaffold accepts an empty dest dir",
      len(scaffold_project("minimal", _empty)) >= 3)

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
