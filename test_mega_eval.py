"""tests for the eval harness. fake run_fn only, no api key needed."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval import load_tasks, run_task, run_benchmark, format_report

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


def write_tasks(data, raw=None):
    # write a task file, raw text wins over the parsed data
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        f.write(raw if raw is not None else json.dumps(data))
    return path


def expect_error(name, fn):
    # fn must raise ValueError with a non-empty message
    try:
        fn()
    except ValueError as e:
        check(name, bool(str(e)))
        return
    check(name, False)


GOOD = [
    {"name": "alpha", "task": "say hello", "expect": ["hello"]},
    {"name": "beta", "task": "say bye", "expect": ["bye"],
     "verify_command": "true", "max_steps": 5},
]

# load_tasks happy path

path = write_tasks(GOOD)
tasks = load_tasks(path)
check("load_tasks returns both tasks", len(tasks) == 2)
check("load_tasks keeps name and prompt",
      tasks[0]["name"] == "alpha" and tasks[0]["task"] == "say hello")
check("load_tasks keeps expect list", tasks[0]["expect"] == ["hello"])
check("load_tasks keeps optional verify_command",
      tasks[1].get("verify_command") == "true")
check("load_tasks keeps optional max_steps",
      tasks[1].get("max_steps") == 5)
check("load_tasks drops no-verify key when absent",
      "verify_command" not in tasks[0])
check("load_tasks accepts an empty list", load_tasks(write_tasks([])) == [])

# load_tasks validation

expect_error("load_tasks rejects a non-list top level",
             lambda: load_tasks(write_tasks({"name": "x"})))
expect_error("load_tasks rejects bad json",
             lambda: load_tasks(write_tasks(None, raw="{not json")))
expect_error("load_tasks rejects a non-object task",
             lambda: load_tasks(write_tasks(["nope"])))
expect_error("load_tasks rejects a missing name",
             lambda: load_tasks(write_tasks(
                 [{"task": "t", "expect": []}])))
expect_error("load_tasks rejects a blank name",
             lambda: load_tasks(write_tasks(
                 [{"name": "  ", "task": "t", "expect": []}])))
expect_error("load_tasks rejects a missing task prompt",
             lambda: load_tasks(write_tasks(
                 [{"name": "x", "expect": []}])))
expect_error("load_tasks rejects a missing expect list",
             lambda: load_tasks(write_tasks(
                 [{"name": "x", "task": "t"}])))
expect_error("load_tasks rejects expect not a list",
             lambda: load_tasks(write_tasks(
                 [{"name": "x", "task": "t", "expect": "hello"}])))
expect_error("load_tasks rejects a non-string expect item",
             lambda: load_tasks(write_tasks(
                 [{"name": "x", "task": "t", "expect": [1]}])))
expect_error("load_tasks rejects a non-string verify_command",
             lambda: load_tasks(write_tasks(
                 [{"name": "x", "task": "t", "expect": [],
                   "verify_command": 3}])))
expect_error("load_tasks rejects a bad max_steps",
             lambda: load_tasks(write_tasks(
                 [{"name": "x", "task": "t", "expect": [],
                   "max_steps": 0}])))

# run_task scoring

seen = {}


def fake_run(text):
    seen["text"] = text
    return "hello world, goodbye"


r = run_task({"name": "a", "task": "say hello", "expect": ["hello"]},
             fake_run)
check("run_task calls run_fn with the task text",
      seen["text"] == "say hello")
check("run_task passes when all expect items are present",
      r["passed"] is True)
check("run_task returns the answer", r["answer"] == "hello world, goodbye")
check("run_task reports no missing items", r["missing"] == [])
check("run_task reports verify_ok None with no command",
      r["verify_ok"] is None)
check("run_task reports steps None for a plain string answer",
      r["steps"] is None)
check("run_task reports no error on success", r["error"] is None)

r = run_task({"name": "a", "task": "t", "expect": ["hello", "nope"]},
             fake_run)
check("run_task fails on a missing expect item", r["passed"] is False)
check("run_task lists only the missing items", r["missing"] == ["nope"])

r = run_task({"name": "a", "task": "t", "expect": ["hello"],
              "verify_command": "true"}, fake_run)
check("run_task passes when verify exits 0",
      r["passed"] is True and r["verify_ok"] is True)

r = run_task({"name": "a", "task": "t", "expect": ["hello"],
              "verify_command": "false"}, fake_run)
check("run_task fails when verify exits non-zero",
      r["passed"] is False and r["verify_ok"] is False)

r = run_task({"name": "a", "task": "t", "expect": ["hello"],
              "verify_command": "exit 3"}, fake_run)
check("run_task fails on any non-zero verify exit",
      r["passed"] is False and r["verify_ok"] is False)

# run_fn may report steps as a tuple or a dict

r = run_task({"name": "a", "task": "t", "expect": ["hello"]},
             lambda t: ("hello there", 7))
check("run_task reads steps from a tuple result",
      r["passed"] is True and r["steps"] == 7)
r = run_task({"name": "a", "task": "t", "expect": ["hello"]},
             lambda t: {"answer": "hello there", "steps": 4})
check("run_task reads steps from a dict result",
      r["passed"] is True and r["steps"] == 4)

# run_task records a raising run_fn instead of crashing

def boom(text):
    raise RuntimeError("model blew up")

r = run_task({"name": "a", "task": "t", "expect": ["hello"]}, boom)
check("run_task fails when run_fn raises", r["passed"] is False)
check("run_task records the run_fn error",
      r["error"] is not None and "blew up" in r["error"])
check("run_task lists expect as missing after a raise",
      r["missing"] == ["hello"])

# run_benchmark aggregates and never crashes on one bad task

def mixed(text):
    if text == "say hello":
        return "hello world"
    if text == "say bye":
        return "see you later"
    raise RuntimeError("nope")

bench = run_benchmark(GOOD, mixed)
check("run_benchmark counts the total", bench["total"] == 2)
check("run_benchmark counts passed", bench["passed"] == 1)
check("run_benchmark pass rate is a float",
      bench["pass_rate"] == 0.5)
check("run_benchmark lists the failed name",
      bench["failed_names"] == ["beta"])
check("run_benchmark keeps one result per task",
      len(bench["results"]) == 2)

def always_boom(text):
    raise RuntimeError("down")

bench = run_benchmark(GOOD, always_boom)
check("run_benchmark survives every task raising",
      bench["passed"] == 0 and bench["total"] == 2)
check("run_benchmark records errors for failed tasks",
      all(r["error"] for r in bench["results"]))

bench = run_benchmark([], mixed)
check("run_benchmark handles an empty task list",
      bench["passed"] == 0 and bench["total"] == 0
      and bench["pass_rate"] == 0.0 and bench["failed_names"] == [])

# format_report is plain text, one line per task plus aggregate

bench = run_benchmark(GOOD, mixed)
report = format_report(bench)
lines = report.strip().split("\n")
check("format_report has one line per task plus aggregate",
      len(lines) == 3)
check("format_report marks the passing task",
      lines[0].startswith("PASS alpha"))
check("format_report marks the failing task",
      lines[1].startswith("FAIL beta"))
check("format_report names the missing item",
      "missing: bye" in lines[1])
check("format_report ends with the pass rate line",
      "1/2 passed" in lines[2] and "50.0%" in lines[2])
check("format_report has no color codes", "\x1b" not in report)
check("format_report has no em dashes", "\u2014" not in report)
check("eval source has no em dashes",
      "\u2014" not in open(os.path.join(
          os.path.dirname(os.path.abspath(__file__)), "eval.py")).read())

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
