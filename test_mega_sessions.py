"""session reporting tests: fork at step, markdown/html export, diff,
cost report, run log, templates. no api key needed."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sessions
import session_report
from sessions import save_session, load_session, fork_at_step, fork_info
from session_report import (export_markdown, export_html, diff_sessions,
                            cost_report, append_run_log, read_run_log,
                            save_template, start_from_template,
                            list_templates)

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


# temp dirs, never the real ~/.mule dir
sess_dir = tempfile.mkdtemp()
sessions.session_dir = lambda: sess_dir
log_dir = tempfile.mkdtemp()
session_report.run_log_dir = lambda: log_dir
tpl_dir = tempfile.mkdtemp()
session_report.templates_dir = lambda: tpl_dir

hist = [
    {"role": "system", "content": "be helpful"},
    {"role": "user", "content": "write hello.txt with <b>bold</b> text"},
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "write_file",
                                  "arguments": '{"path": "hello.txt"}'}}]},
    {"role": "tool", "content": "wrote hello.txt"},
    {"role": "assistant", "content": "done, hello.txt is written"},
]
save_session("alpha", hist)
save_session("beta", hist[:2] + [
    {"role": "assistant", "content": "a different answer"}])
save_session("plain", [
    {"role": "user", "content": "just a question"},
    {"role": "assistant", "content": "just an answer"}])
save_session("priced", [
    {"role": "user", "content": "count this"},
    {"role": "assistant", "content": "counted", "model": "gpt-4o-mini",
     "usage": {"prompt_tokens": 1000000,
               "completion_tokens": 1000000}}])
save_session("mystery", [
    {"role": "user", "content": "count this too"},
    {"role": "assistant", "content": "counted", "model": "no-such-model",
     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}])

# fork_at_step

fork_at_step("alpha", "alpha-early", 2)
check("fork loads the truncated history",
      load_session("alpha-early") == hist[:2])
check("fork leaves the parent untouched",
      load_session("alpha") == hist)
info = fork_info("alpha-early")
check("fork sidecar records parent and step",
      info is not None and info.get("parent") == "alpha"
      and info.get("step") == 2)
check("fork sidecar file sits next to the session",
      os.path.isfile(os.path.join(sess_dir, "alpha-early.meta.json")))
check("non-fork session has no fork info",
      fork_info("alpha") is None)
fork_at_step("alpha", "alpha-empty", 0)
check("fork at step 0 is an empty history",
      load_session("alpha-empty") == [])
fork_at_step("alpha", "alpha-full", len(hist))
check("fork at the full length keeps everything",
      load_session("alpha-full") == hist)
for bad_args, label in [
        (("alpha", "alpha-early", 1), "fork onto an existing name errors"),
        (("nope", "new-one", 1), "fork of a missing session errors"),
        (("alpha", "alpha", 1), "fork onto itself errors"),
        (("alpha", "too-far", 99), "fork past the end errors"),
        (("alpha", "negative", -1), "fork with a negative step errors")]:
    try:
        fork_at_step(*bad_args)
        check(label, False)
    except ValueError:
        check(label, True)

# export_markdown

md = export_markdown("alpha")
check("markdown names the session", "# mule session: alpha" in md)
check("markdown shows the task",
      "## task" in md and "write hello.txt" in md)
check("markdown shows the tool call",
      "write_file" in md and "hello.txt" in md)
check("markdown shows the tool outcome", "wrote hello.txt" in md)
check("markdown shows the final answer",
      "## final answer" in md and "done, hello.txt is written" in md)
check("markdown skips the system prompt", "be helpful" not in md)
try:
    export_markdown("nope")
    check("markdown export of a missing session errors", False)
except ValueError:
    check("markdown export of a missing session errors", True)

# export_html

ht = export_html("alpha")
check("html is a full document",
      ht.startswith("<!DOCTYPE html>") and "</html>" in ht)
check("html carries inline css", "<style>" in ht)
check("html has no external assets",
      "<link" not in ht and "<script src" not in ht
      and "http://" not in ht and "https://" not in ht)
check("html escapes markup in content", "&lt;b&gt;bold&lt;/b&gt;" in ht)
check("html shows the task and final answer",
      "write hello.txt" in ht and "done, hello.txt is written" in ht)
check("html shows the tool call and outcome",
      "write_file" in ht and "wrote hello.txt" in ht)
check("html matches markdown content",
      "hello.txt" in export_markdown("alpha") and "hello.txt" in ht)

# diff_sessions

same = diff_sessions("alpha", "alpha")
check("diff of a session with itself reports no differences",
      "no differences" in same)
d = diff_sessions("alpha", "beta")
check("diff names both sessions",
      "session: alpha" in d and "session: beta" in d)
check("diff marks the removed answer",
      any(line.startswith("-") and "done, hello.txt" in line
          for line in d.splitlines()))
check("diff marks the added answer",
      any(line.startswith("+") and "a different answer" in line
          for line in d.splitlines()))
try:
    diff_sessions("alpha", "nope")
    check("diff with a missing session errors", False)
except ValueError:
    check("diff with a missing session errors", True)

# cost_report

plain = cost_report(["plain"])
row = plain["sessions"][0]
check("session without usage reports counts only",
      row["messages"] == 2 and row["tool_calls"] == 0
      and row["prompt_tokens"] is None and row["cost"] is None)
check("session without usage has no fabricated cost",
      row["has_usage"] is False)
priced = cost_report(["priced"])
row = priced["sessions"][0]
check("session with usage reports tokens",
      row["prompt_tokens"] == 1000000
      and row["completion_tokens"] == 1000000)
check("session with usage reports the priced cost",
      row["cost"] is not None and abs(row["cost"] - 0.75) < 1e-9)
check("session with usage counts its messages",
      row["messages"] == 2)
mystery = cost_report(["mystery"])
check("unknown model keeps tokens but no cost",
      mystery["sessions"][0]["prompt_tokens"] == 10
      and mystery["sessions"][0]["cost"] is None)
full = cost_report()
check("cost report with no names covers every session",
      len(full["sessions"]) == len(sessions.list_sessions()))
check("total messages sum the sessions",
      full["total"]["messages"]
      == sum(r["messages"] for r in full["sessions"]))
check("total tool calls sum the sessions",
      full["total"]["tool_calls"]
      == sum(r["tool_calls"] for r in full["sessions"])
      and full["total"]["tool_calls"] == 2)
check("total cost sums only priced sessions",
      full["total"]["cost"] is not None
      and abs(full["total"]["cost"] - 0.75) < 1e-9)
none = cost_report(["plain"])
check("total with no usage anywhere reports no cost",
      none["total"]["cost"] is None
      and none["total"]["prompt_tokens"] is None)

# run log

check("empty run log reads back empty", read_run_log() == [])
path = append_run_log({"task": "write hello.txt", "model": "gpt-4o-mini",
                       "steps": 3, "outcome": "done", "cost": 0.01})
check("run log lands in the local log dir",
      os.path.dirname(path) == log_dir
      and os.path.basename(path) == "run_log.jsonl")
append_run_log({"task": "second run", "model": "gpt-4o-mini",
                "steps": 1, "outcome": "failed", "cost": 0.0})
entries = read_run_log()
check("run log reads back both entries", len(entries) == 2)
check("run log keeps the task and outcome",
      entries[0]["task"] == "write hello.txt"
      and entries[0]["outcome"] == "done")
check("run log stamps a timestamp",
      "timestamp" in entries[0] and entries[0]["timestamp"])
check("run log file is one json object per line",
      all(json.loads(line) for line in open(path) if line.strip()))
check("run log is plain jsonl, no network involved",
      open(path).read().count("\n") == 2)
try:
    append_run_log("not a dict")
    check("run log rejects a non-dict entry", False)
except ValueError:
    check("run log rejects a non-dict entry", True)

# templates

check("no templates at first", list_templates() == [])
starter = [{"role": "system", "content": "review code carefully"},
           {"role": "user", "content": "review main.py"}]
save_template("review", starter)
check("template is listed after saving", list_templates() == ["review"])
got = start_from_template("review")
check("template starts a run with its starter messages", got == starter)
got.append({"role": "user", "content": "extra"})
check("starting from a template returns a fresh copy",
      start_from_template("review") == starter)
try:
    start_from_template("nope")
    check("missing template errors", False)
except ValueError:
    check("missing template errors", True)
try:
    save_template("../escape", starter)
    check("template name with a path escape errors", False)
except ValueError:
    check("template name with a path escape errors", True)

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
