"""integration checks for the mega-wave wiring: the new modules
actually connected to tools.py, client.py, and main.py. run with:
python3 test_mega_integration.py. no api key needed, temp dirs only.
"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools import ToolSet
from client import ChatClient
import main as main_mod

PASS = []
FAIL = []


def check(name, cond):
    if cond:
        PASS.append(name)
        print("ok  ", name)
    else:
        FAIL.append(name)
        print("FAIL", name)


def mkroot():
    return tempfile.mkdtemp()


# new tools are registered on every toolset
root = mkroot()
tools = ToolSet(root)
names = {s["function"]["name"] for s in tools.schemas()}
for want in ("ast_search", "web_search2", "git_commit", "run_tests",
             "shell_session", "shell_kill", "multi_edit",
             "delegate_many", "remember", "undo_file"):
    check("tool registered: %s" % want, want in names)

# secret-shaped writes are refused unless --allow-secrets
check("secret write refused",
      tools.write_file("k.txt", "api_key = \"sk-abcdefghij1234567890\"")
      .startswith("refused:"))
open_tools = ToolSet(mkroot(), allow_secrets=True)
check("allow_secrets writes",
      open_tools.write_file("k.txt", "api_key = \"sk-abcdefghij1234567890\"")
      .startswith("wrote"))

# multi-level undo through the toolset
t = ToolSet(mkroot())
t.write_file("a.txt", "one")
t.edit_file("a.txt", "one", "two")
t.edit_file("a.txt", "two", "three")
check("undo_file restores one step",
      t.undo_file("a.txt").startswith("restored")
      and open(os.path.join(t.root, "a.txt")).read() == "two")
check("undo_file restores another step",
      t.undo_file("a.txt").startswith("restored")
      and open(os.path.join(t.root, "a.txt")).read() == "one")
check("undo_file empty stack",
      "nothing to undo" in t.undo_file("a.txt"))

# remember tool writes the project memory file
t2 = ToolSet(mkroot())
check("remember saves a fact",
      t2.remember("uses stdlib only").startswith("remembered")
      and "uses stdlib only" in open(
          os.path.join(t2.root, "MEMORY.md")).read())

# symlink escape is blocked by _resolve
root3 = mkroot()
outside = tempfile.mkdtemp()
target = os.path.join(outside, "secret.txt")
with open(target, "w") as f:
    f.write("nope")
os.symlink(target, os.path.join(root3, "link.txt"))
t3 = ToolSet(root3)
try:
    t3._resolve("link.txt")
    blocked = False
except ValueError:
    blocked = True
check("symlink escape blocked", blocked)
check("legit file still resolves",
      t3._resolve("plain.txt").endswith("plain.txt"))

# token budget stops the loop through make_track
args = argparse.Namespace(model="gpt-4o-mini", quiet=True, json=False,
                          print_mode=False, max_cost=None,
                          token_budget=100)
totals = {"in": 0, "out": 0}
track = main_mod.make_track(args, totals, None)
check("under token budget keeps going",
      track(1, {"prompt_tokens": 60, "completion_tokens": 10}) is False)
check("over token budget stops with a note",
      track(2, {"prompt_tokens": 60, "completion_tokens": 10})
      == "hit token budget")

# capability gating disables vision tools for o-series models
from budgets import unsupported_tools
check("o4-mini loses read_image",
      unsupported_tools("o4-mini", ["read_file", "read_image"])
      == ["read_image"])
check("unknown model keeps everything",
      unsupported_tools("some-future-model", ["read_file", "read_image"])
      == [])

# new cli flags exist
parser = main_mod.build_parser({})
flags = {a for act in parser._actions for a in act.option_strings}
for want in ("--system-file", "--persona", "--project-memory",
             "--token-budget", "--allow-secrets", "--from-template",
             "--fork-step"):
    check("flag exists: %s" % want, want in flags)
check("eval is a subcommand", "eval" in main_mod.SUBCOMMANDS)

# resolve_system: file, persona, project memory
root4 = mkroot()
sysfile = os.path.join(root4, "sys.txt")
with open(sysfile, "w") as f:
    f.write("be terse")
with open(os.path.join(root4, "MEMORY.md"), "w") as f:
    f.write("project fact: uses sqlite\n")
nargs = argparse.Namespace(
    system=None, system_file=sysfile, system_prompt=None,
    append_system=None, persona="terse implementer",
    project_memory=True, root=root4)
system = main_mod.resolve_system(nargs)
check("system-file loads", "be terse" in system)
check("persona prepends", system.index("terse") < system.index("be terse")
      if "terse" in system else False)
check("project memory appends", "uses sqlite" in system)

# client adaptive backoff keeps the old delays on a fresh model
c = ChatClient("http://127.0.0.1:9/v1", "k", "m", backoff=1.0)
check("client has adaptive backoff",
      c._adaptive is not None and c._adaptive.base == 1.0)

# sessions subcommand actions, against a patched session dir
import sessions
import session_report
sessdir = mkroot()
sessions.session_dir = lambda: sessdir
session_report.run_log_dir = lambda: sessdir
session_report.templates_dir = lambda: sessdir
sessions.save_session("s1", [{"role": "user", "content": "do x"},
                             {"role": "assistant", "content": "did x"}])
sessions.save_session("s2", [{"role": "user", "content": "do y"},
                             {"role": "assistant", "content": "did y"}])
check("sessions diff runs",
      main_mod.cmd_sessions(["diff", "s1", "s2"]) == 0)
check("sessions cost runs",
      main_mod.cmd_sessions(["cost"]) == 0)
check("sessions fork-at runs",
      main_mod.cmd_sessions(["fork-at", "s1", "s1b", "1"]) == 0
      and len(sessions.load_session("s1b")) == 1)
check("sessions export md runs",
      main_mod.cmd_sessions(["export", "s1", "md",
                             os.path.join(sessdir, "out.md")]) == 0
      and os.path.isfile(os.path.join(sessdir, "out.md")))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    sys.exit(1)
