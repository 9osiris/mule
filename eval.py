"""built-in eval harness: score agent runs against a task file."""

import json
import subprocess
import tempfile


# load and validate a task file, returns a list of task dicts
def load_tasks(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except ValueError as e:
        raise ValueError("invalid task file %s: %s" % (path, e))
    if not isinstance(data, list):
        raise ValueError("task file %s must hold a list of tasks" % path)
    tasks = []
    for i, raw in enumerate(data):
        label = "task %d" % i
        if not isinstance(raw, dict):
            raise ValueError("%s must be an object" % label)
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("%s is missing a name" % label)
        text = raw.get("task")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("task %s is missing a task prompt" % name)
        expect = raw.get("expect")
        if not isinstance(expect, list):
            raise ValueError("task %s: expect must be a list" % name)
        for item in expect:
            if not isinstance(item, str):
                raise ValueError(
                    "task %s: expect items must be strings" % name)
        task = {"name": name, "task": text, "expect": list(expect)}
        command = raw.get("verify_command")
        if command is not None:
            if not isinstance(command, str) or not command.strip():
                raise ValueError(
                    "task %s: verify_command must be a string" % name)
            task["verify_command"] = command
        steps = raw.get("max_steps")
        if steps is not None:
            if isinstance(steps, bool) or not isinstance(steps, int) \
                    or steps <= 0:
                raise ValueError(
                    "task %s: max_steps must be a positive integer" % name)
            task["max_steps"] = steps
        tasks.append(task)
    return tasks


# split a run_fn result into an answer string and a step count
def _split_result(result):
    if isinstance(result, tuple) and len(result) == 2:
        answer, steps = result
        return ("" if answer is None else str(answer), steps)
    if isinstance(result, dict) and "answer" in result:
        answer = result["answer"]
        return ("" if answer is None else str(answer),
                result.get("steps"))
    if result is None:
        return "", None
    return str(result), None


# run one verify command in an empty temp dir, True on exit 0
def _run_verify(command):
    try:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(command, shell=True, cwd=tmp,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=60)
        return proc.returncode == 0
    except Exception:
        return False


# score one task against a run_fn, never raises for a run_fn failure
def run_task(task, run_fn):
    name = task.get("name", "") if isinstance(task, dict) else ""
    expect = task.get("expect", []) if isinstance(task, dict) else []
    text = task.get("task", "") if isinstance(task, dict) else ""
    try:
        answer, steps = _split_result(run_fn(text))
    except Exception as e:
        return {"name": name, "passed": False, "answer": "",
                "missing": list(expect), "verify_ok": None,
                "steps": None, "error": str(e)}
    missing = [item for item in expect if item not in answer]
    command = task.get("verify_command") if isinstance(task, dict) else None
    verify_ok = _run_verify(command) if command else None
    passed = not missing and verify_ok is not False
    return {"name": name, "passed": passed, "answer": answer,
            "missing": missing, "verify_ok": verify_ok,
            "steps": steps, "error": None}


# run every task, a failing task never stops the benchmark
def run_benchmark(tasks, run_fn):
    results = []
    for task in tasks:
        try:
            results.append(run_task(task, run_fn))
        except Exception as e:
            name = task.get("name", "") if isinstance(task, dict) else ""
            expect = task.get("expect", []) \
                if isinstance(task, dict) else []
            results.append({"name": name, "passed": False, "answer": "",
                            "missing": list(expect), "verify_ok": None,
                            "steps": None, "error": str(e)})
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    return {"results": results, "passed": passed, "total": total,
            "pass_rate": (passed / total) if total else 0.0,
            "failed_names": [r["name"] for r in results
                             if not r["passed"]]}


# one line per task plus an aggregate line, plain text only
def format_report(benchmark):
    lines = []
    for r in benchmark["results"]:
        line = "%s %s" % ("PASS" if r["passed"] else "FAIL", r["name"])
        if r["missing"]:
            line += " (missing: %s)" % ", ".join(r["missing"])
        if r.get("verify_ok") is False:
            line += " (verify failed)"
        if r.get("error"):
            line += " (error: %s)" % r["error"]
        lines.append(line)
    total = benchmark["total"]
    rate = benchmark["pass_rate"] * 100
    lines.append("%d/%d passed (%.1f%% pass rate)"
                 % (benchmark["passed"], total, rate))
    return "\n".join(lines) + "\n"
