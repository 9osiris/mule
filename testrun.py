"""run tests and return a structured summary."""
import re
import subprocess

from tools import DANGEROUS, CRITICAL, _matches, MAX_OUTPUT

MAX_TEST_OUTPUT = 20_000
FAIL_RE = re.compile(r"^(FAIL|ERROR):\s+(\S+)", re.M)
PYTEST_FAIL_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)
RAN_RE = re.compile(r"Ran (\d+) tests?")
FAILED_RE = re.compile(r"FAILED \(failures=(\d+), errors=(\d+)\)")
FAILED_SHORT_RE = re.compile(r"FAILED \(failures=(\d+)\)")
ERRORS_SHORT_RE = re.compile(r"FAILED \(errors=(\d+)\)")


def _parse(output):
    # counts and failing names from unittest or pytest output
    failing = []
    for m in FAIL_RE.finditer(output):
        failing.append(m.group(2))
    for m in PYTEST_FAIL_RE.finditer(output):
        if m.group(1) not in failing:
            failing.append(m.group(1))
    total = 0
    m = RAN_RE.search(output)
    if m:
        total = int(m.group(1))
    failed = 0
    errors = 0
    m = FAILED_RE.search(output)
    if m:
        failed, errors = int(m.group(1)), int(m.group(2))
    else:
        m = FAILED_SHORT_RE.search(output)
        if m:
            failed = int(m.group(1))
        m = ERRORS_SHORT_RE.search(output)
        if m:
            errors = int(m.group(1))
    if total and not failed and not errors:
        # unittest FAIL/ERROR lines without a FAILED summary
        failed = len([f for f in failing])
    if total:
        passed = max(0, total - failed - errors)
    else:
        passed = 0
        total = passed + failed + errors
    # pytest short summary: "3 passed, 1 failed in 0.5s"
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+) error", output)
    if m:
        errors = int(m.group(1))
    if passed or failed or errors:
        total = passed + failed + errors
    return passed, failed, errors, total, failing


def register(toolset):
    def run_tests(command="python3 -m unittest discover", timeout="120"):
        cmd = (command or "").strip()
        if not cmd:
            return "error: empty test command"
        danger = _matches(DANGEROUS, cmd)
        if danger:
            return "refused: looks like %s, not running it" % danger
        critical = _matches(CRITICAL, cmd)
        if critical:
            if toolset.confirm_critical \
                    and toolset.confirm_critical(cmd):
                pass
            else:
                return ("refused: %s needs the literal word 'yes' "
                        "in --ask mode, not running it" % critical)
        elif toolset.confirm and not toolset.confirm(cmd):
            return "declined: the test command was not run"
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 120
        timeout = min(max(timeout, 1), 600)
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=toolset.root,
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "error: tests timed out after %ss" % timeout
        except OSError as e:
            return "error: could not start tests: %s" % e
        out = (proc.stdout or "") + (proc.stderr or "")
        passed, failed, errors, total, failing = _parse(out)
        lines = ["passed: %d" % passed, "failed: %d" % failed,
                 "errors: %d" % errors, "total: %d" % total,
                 "exit: %d" % proc.returncode]
        if failing:
            lines.append("failing tests:")
            for name in failing:
                lines.append("- %s" % name)
        if len(out) > MAX_TEST_OUTPUT:
            out = out[:MAX_TEST_OUTPUT] + "\n...[truncated]"
        lines.append("output:")
        lines.append(out.rstrip() or "(no output)")
        return "\n".join(lines)

    toolset.tools["run_tests"] = {
        "description": "run a test command and return passed, failed, "
                       "and error counts plus failing test names",
        "parameters": {"command": "test command, default "
                                  "python3 -m unittest discover",
                       "timeout": "seconds, default 120"},
        "run": run_tests,
    }
