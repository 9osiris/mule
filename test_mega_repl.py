"""tests for the mega repl wave: history, branch, sessions,
export, persona, and multi-line continuation. no api key needed."""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import repl
import sessions

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


class FakeTools:
    def __init__(self):
        self.tools = {"read_file": {"description": "read a file"}}
        self.disabled = set()

    def undo_last(self):
        return "nothing to undo"

    def disable_tool(self, name):
        self.disabled.add(name)

    def enable_tool(self, name):
        self.disabled.discard(name)


def make_ctx(tmp_hist=None):
    out = []
    ctx = {"write": out.append, "tools": FakeTools(),
           "totals": {"in": 10, "out": 5}, "model": "gpt-4o-mini",
           "save_fn": lambda n: "/tmp/" + n,
           "commands": {}, "last_task": None, "version": "test"}
    ctx["_out"] = out
    return ctx


def test_history():
    tmp = tempfile.mkdtemp(prefix="mule-hist-")
    path = os.path.join(tmp, "repl_history")
    old = repl.HISTORY_PATH
    repl.HISTORY_PATH = path
    try:
        check("load history empty at first", repl.load_history() == [])
        repl.append_history("first line")
        repl.append_history("second line")
        repl.append_history("   ")
        repl.append_history(None)
        check("history keeps entered lines",
              repl.load_history() == ["first line", "second line"])
        for i in range(1005):
            repl.append_history("line %d" % i)
        kept = repl.load_history()
        check("history capped at 1000 lines", len(kept) == 1000)
        check("history cap keeps the newest",
              kept[0] == "line 5" and kept[-1] == "line 1004")
        check("history file exists on disk", os.path.isfile(path))
    finally:
        repl.HISTORY_PATH = old


def test_history_never_crashes():
    tmp = tempfile.mkdtemp(prefix="mule-hist-")
    old = repl.HISTORY_PATH
    repl.HISTORY_PATH = tmp  # a directory, writes must fail softly
    try:
        repl.append_history("boom")
        check("history write failure does not raise", True)
        check("history load failure returns empty",
              repl.load_history() == [])
    finally:
        repl.HISTORY_PATH = old


def test_branch_and_sessions():
    tmp = tempfile.mkdtemp(prefix="mule-sess-")
    old_dir = sessions.session_dir
    sessions.session_dir = lambda: tmp
    try:
        sessions.save_session("parent", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"}])
        ctx = make_ctx()
        out = ctx["_out"]
        repl.handle_slash("/sessions", ctx)
        check("sessions lists the parent",
              any("parent" in s for s in out))
        out.clear()
        repl.handle_slash("/branch child --from parent", ctx)
        check("branch reports the new session",
              any("child" in s for s in out))
        check("branch file exists",
              "child" in sessions.list_sessions())
        check("branch keeps the parent history",
              sessions.load_session("child") ==
              sessions.load_session("parent"))
        check("branch records its parent",
              sessions.branch_parent("child") == "parent")
        out.clear()
        repl.handle_slash("/branch other --from missing", ctx)
        check("branch of a missing parent errors",
              any("error" in s for s in out))
        out.clear()
        repl.handle_slash("/branch lonely", ctx)
        check("branch without --from shows usage",
              any("usage" in s for s in out))
        out.clear()
        sessions.save_session("empty-check", [])
        repl.handle_slash("/sessions", ctx)
        check("sessions lists both names",
              any("child" in s for s in out) and
              any("parent" in s for s in out))
    finally:
        sessions.session_dir = old_dir


def test_export_without_report():
    ctx = make_ctx()
    out = ctx["_out"]
    old = sys.modules.get("session_report")
    sys.modules["session_report"] = None  # blocked import
    try:
        repl.handle_slash("/export parent md", ctx)
    finally:
        if old is None:
            sys.modules.pop("session_report", None)
        else:
            sys.modules["session_report"] = old
    check("export without session_report is a clean message",
          any("not available" in s for s in out))
    out.clear()
    repl.handle_slash("/export parent", ctx)
    check("export without a format shows usage",
          any("usage" in s for s in out))
    out.clear()
    repl.handle_slash("/export parent pdf", ctx)
    check("export with a bad format shows usage",
          any("usage" in s for s in out))


def test_export_with_real_report():
    tmp = tempfile.mkdtemp(prefix="mule-sess-")
    old_dir = sessions.session_dir
    sessions.session_dir = lambda: tmp
    expdir = tempfile.mkdtemp(prefix="mule-exp-")
    try:
        sessions.save_session("parent", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"}])
        ctx = make_ctx()
        ctx["export_dir"] = expdir
        out = ctx["_out"]
        repl.handle_slash("/export parent md", ctx)
        md_path = os.path.join(expdir, "parent.md")
        check("export md writes a file",
              os.path.isfile(md_path))
        check("export md keeps the transcript",
              "hello" in open(md_path).read())
        check("export md reports the path",
              any("parent.md" in s for s in out))
        out.clear()
        repl.handle_slash("/export parent html", ctx)
        ht_path = os.path.join(expdir, "parent.html")
        check("export html writes a file",
              os.path.isfile(ht_path))
        check("export html keeps the transcript",
              "hello" in open(ht_path).read())
        out.clear()
        repl.handle_slash("/export missing md", ctx)
        check("export of a missing session errors cleanly",
              any("error" in s for s in out))
    finally:
        sessions.session_dir = old_dir


def test_export_with_report():
    fake = types.ModuleType("session_report")
    fake.export = lambda name, fmt: "/tmp/%s.%s" % (name, fmt)
    sys.modules["session_report"] = fake
    try:
        ctx = make_ctx()
        out = ctx["_out"]
        repl.handle_slash("/export parent html", ctx)
        check("export calls session_report",
              any("/tmp/parent.html" in s for s in out))
    finally:
        sys.modules.pop("session_report", None)


def test_persona():
    ctx = make_ctx()
    out = ctx["_out"]
    old = sys.modules.get("personas")
    sys.modules["personas"] = None  # blocked import
    try:
        repl.handle_slash("/persona", ctx)
    finally:
        if old is None:
            sys.modules.pop("personas", None)
        else:
            sys.modules["personas"] = old
    check("persona without the module is a clean message",
          any("not available" in s for s in out))
    out.clear()
    repl.handle_slash("/persona", ctx)
    check("persona with no arg lists personas",
          any("explainer" in s for s in out) and
          any("tester" in s for s in out))
    out.clear()
    repl.handle_slash("/persona explainer", ctx)
    check("persona switches and stores the name",
          ctx.get("persona") == "explainer" and
          "patient explainer" in ctx.get("persona_text", ""))
    check("persona switch confirms",
          any("persona: explainer" in s for s in out))
    out.clear()
    repl.handle_slash("/persona nope", ctx)
    check("unknown persona errors cleanly",
          any("error" in s for s in out))
    check("failed persona switch keeps the old one",
          ctx.get("persona") == "explainer")


def test_needs_continuation():
    check("plain line needs nothing",
          not repl.needs_continuation("hello world"))
    check("trailing backslash needs more",
          repl.needs_continuation("hello \\"))
    check("odd fence count needs more",
          repl.needs_continuation("explain ```python"))
    check("closed fence needs nothing",
          not repl.needs_continuation("```code```"))
    check("two fences in one line needs nothing",
          not repl.needs_continuation("a ```x``` b"))
    check("backslash join drops the newline",
          repl.join_continued(["hello \\", "world"]) == "hello world")
    check("fence join keeps newlines",
          repl.join_continued(["a ```", "b", "```"]) == "a ```\nb\n```")


def _run_loop(lines):
    tmp = tempfile.mkdtemp(prefix="mule-hist-")
    old = repl.HISTORY_PATH
    repl.HISTORY_PATH = os.path.join(tmp, "repl_history")
    seen = []
    script = list(lines)

    def read_line(prompt):
        if not script:
            raise EOFError
        return script.pop(0)

    try:
        repl.repl_loop(read_line, lambda s: None,
                       seen.append, lambda line: None)
    finally:
        repl.HISTORY_PATH = old
    return seen


def test_loop_continuation():
    seen = _run_loop(["hello \\", "world"])
    check("backslash continuation joins one task",
          seen == ["hello world"])
    seen = _run_loop(["explain ```", "some code", "```"])
    check("unclosed fence continuation joins one task",
          seen == ["explain ```\nsome code\n```"])
    seen = _run_loop(["```python", "x = 1", "```"])
    check("existing triple backtick block still works",
          seen == ["```python\nx = 1\n```"])
    seen = _run_loop(["one", "two"])
    check("plain lines stay separate tasks", seen == ["one", "two"])


def test_loop_saves_history():
    tmp = tempfile.mkdtemp(prefix="mule-hist-")
    path = os.path.join(tmp, "repl_history")
    old = repl.HISTORY_PATH
    repl.HISTORY_PATH = path
    script = ["first task", "second task"]

    def read_line(prompt):
        if not script:
            raise EOFError
        return script.pop(0)

    try:
        repl.repl_loop(read_line, lambda s: None,
                       lambda line: None, lambda line: None)
        check("loop saves each entered line",
              repl.load_history() == ["first task", "second task"])
    finally:
        repl.HISTORY_PATH = old


def test_help_lists_new_commands():
    ctx = make_ctx()
    out = ctx["_out"]
    repl.handle_slash("/help", ctx)
    text = "\n".join(out)
    check("help lists branch", "/branch" in text)
    check("help lists sessions", "/sessions" in text)
    check("help lists export", "/export" in text)
    check("help lists persona", "/persona" in text)
    check("help has no em dash", "\u2014" not in text)


def main():
    test_history()
    test_history_never_crashes()
    test_branch_and_sessions()
    test_export_without_report()
    test_export_with_report()
    test_export_with_real_report()
    test_persona()
    test_needs_continuation()
    test_loop_continuation()
    test_loop_saves_history()
    test_help_lists_new_commands()
    print("%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print("FAIL: %s" % name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
