"""tests for the mega tools: ast search, web search2, git commit,
run_tests, shell sessions, multi_edit. no api key needed."""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools import ToolSet
import search_tools
import git_tools
import testrun
import shell_session
import multiedit

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


def fresh():
    root = tempfile.mkdtemp()
    tools = ToolSet(root)
    search_tools.register(tools)
    git_tools.register(tools)
    testrun.register(tools)
    shell_session.register(tools)
    multiedit.register(tools)
    return root, tools


def write(root, rel, text):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w") as f:
        f.write(text)
    return full


def read(root, rel):
    with open(os.path.join(root, rel)) as f:
        return f.read()


# every new tool is registered and shows up in schemas

root, tools = fresh()
names = [t["function"]["name"] for t in tools.schemas()]
for want in ("ast_search", "web_search2", "git_commit", "run_tests",
             "shell_session", "shell_kill", "multi_edit"):
    check("registered: %s" % want, want in names)
req_ok = all(
    isinstance(t["function"]["parameters"].get("required"), list)
    and set(t["function"]["parameters"]["required"])
    == set(t["function"]["parameters"]["properties"])
    for t in tools.schemas()
    if t["function"]["name"] in ("ast_search", "web_search2", "git_commit",
                                 "run_tests", "shell_session",
                                 "shell_kill", "multi_edit"))
check("new tool schemas carry an explicit required array", req_ok)

# ast_search: functions, classes, methods by name pattern

aroot, atools = fresh()
write(aroot, "a.py", "def foo():\n    pass\n\n\nclass Bar:\n"
      "    def baz(self):\n        pass\n\n    def qux(self):\n        pass\n")
write(aroot, "b.py", "def foo_helper():\n    pass\n\n\ndef other():\n    pass\n")
write(aroot, "bad.py", "def broken(:\n")
write(aroot, "note.txt", "def foo():\n")

out = atools.call("ast_search", {"pattern": "foo", "path": "."})
check("ast_search finds functions by pattern",
      "a.py:1: function foo" in out
      and "b.py:1: function foo_helper" in out)
check("ast_search skips non-python files",
      "note.txt" not in out)
check("ast_search skips files with syntax errors",
      "bad.py" not in out)
out = atools.call("ast_search", {"pattern": "Bar", "path": "."})
check("ast_search finds classes",
      "a.py:5: class Bar" in out)
out = atools.call("ast_search", {"pattern": "baz", "path": "."})
check("ast_search finds methods under their class",
      "a.py:6: method Bar.baz" in out)
out = atools.call("ast_search", {"pattern": "baz", "path": ".",
                                 "kind": "function"})
check("ast_search kind=function excludes methods",
      "method" not in out and "no matches" in out)
out = atools.call("ast_search", {"pattern": "baz", "path": ".",
                                 "kind": "method"})
check("ast_search kind=method finds methods",
      "method Bar.baz" in out)
out = atools.call("ast_search", {"pattern": "Bar", "path": ".",
                                 "kind": "class"})
check("ast_search kind=class finds classes",
      "class Bar" in out)
check("ast_search no matches",
      "no matches" in atools.call("ast_search",
                                  {"pattern": "zzz_nope", "path": "."}))
check("ast_search bad regex is an error",
      atools.call("ast_search", {"pattern": "[", "path": "."})
      .startswith("error:"))
check("ast_search empty pattern is an error",
      atools.call("ast_search", {"pattern": "", "path": "."})
      .startswith("error:"))
check("ast_search outside root rejected",
      "escapes project root" in atools.call(
          "ast_search", {"pattern": "x", "path": "../.."}))
check("ast_search bad kind is an error",
      atools.call("ast_search", {"pattern": "x", "path": ".",
                                 "kind": "bogus"}).startswith("error:"))

# web_search2: engines, parsing, and the 24h cache

DDG_PAGE = """
<html><body><table>
<tr><td>1.</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc"
 class='result-link'>First Result</a>
</td></tr>
<tr><td>&nbsp;</td>
<td class='result-snippet'>the first snippet, with words.</td></tr>
</table></body></html>
"""
BING_PAGE = """
<html><body><ol>
<li class="b_algo"><h2><a href="https://example.com/b">Bing Title</a></h2>
<p>bing snippet here</p></li>
<li class="b_algo"><h2><a href="https://www.bing.com/search?q=x">Bing Internal</a></h2></li>
</ol></body></html>
"""
BRAVE_PAGE = """
<html><body><div>
<a href="https://example.com/c">Brave Title</a>
<a href="https://search.brave.com/search?q=x">Brave Internal</a>
</div></body></html>
"""

real_fetch = search_tools._fetch_page


def stub_fetch(url):
    if "bing.com" in url:
        return BING_PAGE
    if "brave.com" in url:
        return BRAVE_PAGE
    return DDG_PAGE


sroot, stools = fresh()
search_tools._fetch_page = stub_fetch
try:
    out = stools.call("web_search2", {"query": "fake",
                                      "engine": "duckduckgo"})
    check("web_search2 duckduckgo returns titles",
          "First Result" in out)
    check("web_search2 duckduckgo returns urls",
          "https://example.com/a" in out)
    check("web_search2 duckduckgo returns snippets",
          "the first snippet" in out)
    out = stools.call("web_search2", {"query": "fake", "engine": "bing"})
    check("web_search2 bing parses html results",
          "Bing Title" in out and "https://example.com/b" in out)
    check("web_search2 bing skips internal links",
          "Bing Internal" not in out)
    out = stools.call("web_search2", {"query": "fake", "engine": "brave"})
    check("web_search2 brave parses html results",
          "Brave Title" in out and "https://example.com/c" in out)
    check("web_search2 brave skips internal links",
          "Brave Internal" not in out)
    cache_file = search_tools._cache_path(sroot)
    check("web_search2 writes a cache file",
          os.path.isfile(cache_file))
    # the cache serves the second call even when fetching breaks
    search_tools._fetch_page = lambda url: (_ for _ in ()).throw(
        RuntimeError("network is down"))
    out = stools.call("web_search2", {"query": "fake",
                                      "engine": "duckduckgo"})
    check("web_search2 serves a cached result when fetch fails",
          "First Result" in out and "(cached result)" in out)
    # a stale cache entry (older than 24h) is ignored
    with open(cache_file) as f:
        cache_data = json.load(f)
    for entry in cache_data.values():
        entry["ts"] = time.time() - 90000
    with open(cache_file, "w") as f:
        json.dump(cache_data, f)
    search_tools._fetch_page = stub_fetch
    out = stools.call("web_search2", {"query": "fake",
                                      "engine": "duckduckgo"})
    check("web_search2 ignores a stale cache entry",
          "First Result" in out and "(cached result)" not in out)
finally:
    search_tools._fetch_page = real_fetch

check("web_search2 unknown engine is an error",
      stools.call("web_search2", {"query": "x", "engine": "yahoo"})
      .startswith("error:"))
check("web_search2 empty query is an error",
      stools.call("web_search2", {"query": ""}).startswith("error:"))
search_tools._fetch_page = lambda url: (_ for _ in ()).throw(
    RuntimeError("boom"))
try:
    check("web_search2 fetch failure is an error, not a crash",
          stools.call("web_search2", {"query": "never-seen-q",
                                      "engine": "bing"})
          .startswith("error:"))
finally:
    search_tools._fetch_page = real_fetch

# git_commit: stage paths or all, refuse bad states

groot, gtools = fresh()
subprocess.run(["git", "init", "-q"], cwd=groot, check=True)
subprocess.run(["git", "config", "user.email", "t@example.com"],
               cwd=groot, check=True)
subprocess.run(["git", "config", "user.name", "Tester"],
               cwd=groot, check=True)
write(groot, "a.txt", "one\n")
r = gtools.call("git_commit", {"message": "add a",
                               "paths": json.dumps(["a.txt"])})
check("git_commit commits a staged path", r.startswith("committed:"))
check("git_commit landed in the log",
      "add a" in subprocess.run(
          ["git", "log", "--oneline"], cwd=groot, capture_output=True,
          text=True).stdout)
check("git_commit empty message refused",
      gtools.call("git_commit", {"message": "   ",
                                 "paths": json.dumps(["a.txt"])})
      .startswith("error:"))
check("git_commit clean tree has nothing to commit",
      "nothing to commit" in gtools.call(
          "git_commit", {"message": "noop", "all": "true"}))
check("git_commit missing path is an error",
      gtools.call("git_commit", {"message": "x",
                                 "paths": json.dumps(["nope.txt"])})
      .startswith("error:"))
check("git_commit path outside root refused",
      "escapes project root" in gtools.call(
          "git_commit", {"message": "x",
                         "paths": json.dumps(["../evil.txt"])}))
check("git_commit with no paths and no all flag is an error",
      gtools.call("git_commit", {"message": "x"}).startswith("error:"))
write(groot, "a.txt", "one changed\n")
write(groot, "b.txt", "two\n")
r = gtools.call("git_commit", {"message": "only b",
                               "paths": json.dumps(["b.txt"])})
check("git_commit refuses a subset when other tracked files are dirty",
      r.startswith("error:") and "a.txt" in r)
r = gtools.call("git_commit", {"message": "all of it", "all": "true"})
check("git_commit all=true commits everything",
      r.startswith("committed:"))
check("git_commit all=true landed both files",
      "all of it" in subprocess.run(
          ["git", "log", "--oneline"], cwd=groot, capture_output=True,
          text=True).stdout)
nroot, ntools = fresh()
check("git_commit outside a repo is an error",
      ntools.call("git_commit", {"message": "x", "all": "true"})
      .startswith("error:"))

# run_tests: structured counts from unittest output

troot, ttools = fresh()
write(troot, "test_sample.py",
      "import unittest\n\n\nclass T(unittest.TestCase):\n"
      "    def test_pass_one(self):\n"
      "        self.assertEqual(1, 1)\n\n"
      "    def test_pass_two(self):\n"
      "        self.assertTrue(True)\n\n"
      "    def test_fail_one(self):\n"
      "        self.assertEqual(1, 2)\n")
out = ttools.call("run_tests", {})
check("run_tests counts passed", "passed: 2" in out)
check("run_tests counts failed", "failed: 1" in out)
check("run_tests counts errors", "errors: 0" in out)
check("run_tests counts total", "total: 3" in out)
check("run_tests names the failing test",
      "test_fail_one" in out)
proot, ptools = fresh()
write(proot, "test_sample.py",
      "import unittest\n\n\nclass T(unittest.TestCase):\n"
      "    def test_ok(self):\n"
      "        self.assertEqual(1, 1)\n")
out = ptools.call("run_tests", {})
check("run_tests all passing", "passed: 1" in out
      and "failed: 0" in out and "exit: 0" in out)
check("run_tests empty command is an error",
      ttools.call("run_tests", {"command": ""}).startswith("error:"))
check("run_tests refuses a critical command",
      "refused" in ttools.call("run_tests", {"command": "rm -rf /"}))
out = ttools.call("run_tests",
                  {"command": "python3 -c \"print('x' * 30000)\""})
check("run_tests caps huge output", "[truncated]" in out)
out = ttools.call("run_tests",
                  {"command": "python3 -m unittest discover -s .",
                   "timeout": "1"})
check("run_tests custom command works", "passed:" in out)

# shell_session: cwd and env persist, sessions stay separate

hroot, htools = fresh()
os.makedirs(os.path.join(hroot, "sub"))
out = htools.call("shell_session", {"session": "s1",
                                    "command": "cd sub"})
check("shell_session cd runs", "error" not in out)
out = htools.call("shell_session", {"session": "s1",
                                    "command": "pwd"})
check("shell_session cwd persists across calls",
      out.rstrip().endswith("sub"))
out = htools.call("shell_session", {"session": "s1",
                                    "command": "cd sub && pwd"})
check("shell_session cd with && works",
      out.rstrip().endswith("sub"))
htools.call("shell_session", {"session": "s1",
                              "command": "export FOO=bar"})
out = htools.call("shell_session", {"session": "s1",
                                    "command": "echo $FOO"})
check("shell_session exported env persists", "bar" in out)
out = htools.call("shell_session", {"session": "s2",
                                    "command": "echo $FOO"})
check("shell_session env is per session", "bar" not in out)
out = htools.call("shell_session", {"session": "s1", "command": ""})
check("shell_session status shows cwd and env",
      "sub" in out and "FOO" in out)
check("shell_session cd outside root refused",
      "escapes project root" in htools.call(
          "shell_session", {"session": "s1", "command": "cd ../.."}))
check("shell_session bad name is an error",
      htools.call("shell_session", {"session": "bad name!",
                                    "command": "pwd"}).startswith("error:"))
check("shell_session refuses a critical command",
      "refused" in htools.call("shell_session", {"session": "s1",
                                                 "command": "rm -rf /"}))
check("shell_kill closes a session",
      htools.call("shell_kill", {"session": "s1"})
      == "closed session s1")
check("shell_kill on a missing session is an error",
      htools.call("shell_kill", {"session": "s1"}).startswith("error:"))
out = htools.call("shell_session", {"session": "s1",
                                    "command": "echo $FOO"})
check("shell_session restarts clean after kill", "bar" not in out)

# multi_edit: all or nothing, exactly once per old string

mroot, mtools = fresh()
write(mroot, "f.txt", "a = 1\nb = 2\nc = 3\n")
r = mtools.call("multi_edit", {"path": "f.txt", "edits": json.dumps([
    {"old": "a = 1", "new": "a = 10"},
    {"old": "c = 3", "new": "c = 30"},
])})
check("multi_edit applies all pairs", r == "applied 2 edits to f.txt")
check("multi_edit changed both spots",
      read(mroot, "f.txt") == "a = 10\nb = 2\nc = 30\n")
write(mroot, "g.txt", "a = 1\nb = 2\n")
r = mtools.call("multi_edit", {"path": "g.txt", "edits": json.dumps([
    {"old": "a = 1", "new": "a = 10"},
    {"old": "nope", "new": "x"},
])})
check("multi_edit zero match is an error",
      r.startswith("error:") and "nothing applied" in r)
check("multi_edit leaves the file alone on failure",
      read(mroot, "g.txt") == "a = 1\nb = 2\n")
write(mroot, "h.txt", "x = 1\nx = 2\n")
r = mtools.call("multi_edit", {"path": "h.txt", "edits": json.dumps([
    {"old": "x =", "new": "y ="},
])})
check("multi_edit multiple matches is an error",
      r.startswith("error:") and "2 times" in r)
check("multi_edit leaves the file alone on multiple matches",
      read(mroot, "h.txt") == "x = 1\nx = 2\n")
write(mroot, "i.txt", "a = 1\n")
r = mtools.call("multi_edit", {"path": "i.txt", "edits": json.dumps([
    {"old": "a = 1", "new": "b = 2"},
    {"old": "a = 1", "new": "c = 3"},
])})
check("multi_edit duplicate old fails all or nothing",
      r.startswith("error:"))
check("multi_edit duplicate old leaves the file alone",
      read(mroot, "i.txt") == "a = 1\n")
check("multi_edit empty edits is an error",
      mtools.call("multi_edit", {"path": "f.txt", "edits": "[]"})
      .startswith("error:"))
check("multi_edit bad json is an error",
      mtools.call("multi_edit", {"path": "f.txt", "edits": "nope"})
      .startswith("error:"))
check("multi_edit missing file is an error",
      mtools.call("multi_edit", {"path": "nope.txt", "edits": json.dumps([
          {"old": "a", "new": "b"}])}).startswith("error:"))
check("multi_edit outside root rejected",
      "escapes project root" in mtools.call(
          "multi_edit", {"path": "../evil.txt", "edits": json.dumps([
              {"old": "a", "new": "b"}])}))
check("multi_edit empty old is an error",
      mtools.call("multi_edit", {"path": "f.txt", "edits": json.dumps([
          {"old": "", "new": "b"}])}).startswith("error:"))

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
