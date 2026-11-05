"""persistent shell sessions, cwd and env survive across calls."""
import os
import re
import subprocess

from tools import DANGEROUS, CRITICAL, _matches, MAX_OUTPUT

SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S*)$")
EXPORT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?$")


def _sessions(toolset):
    # sessions live on the toolset, one dict per toolset
    sessions = getattr(toolset, "_shell_sessions", None)
    if sessions is None:
        sessions = {}
        toolset._shell_sessions = sessions
    return sessions


def _get(toolset, name):
    # a session starts in the project root with no extra env
    sessions = _sessions(toolset)
    if name not in sessions:
        sessions[name] = {"cwd": toolset.root, "env": {}}
    return sessions[name]


def _inside_root(toolset, full):
    return os.path.commonpath([full, toolset.root]) == toolset.root


def _run_one(toolset, session, cmd, timeout):
    # run one command in the session cwd and env, cap output
    env = dict(os.environ)
    env.update(session["env"])
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=session["cwd"], env=env,
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "error: command timed out after %ss" % timeout
    except OSError as e:
        return "error: could not start: %s" % e
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + "\n...[truncated]"
    return "exit %d\n%s" % (proc.returncode, out.rstrip() or "(no output)")


def _do_cd(toolset, session, target):
    # cd stays inside the root, bare cd goes back to the root
    target = (target or "").strip()
    if not target or target == "~":
        session["cwd"] = toolset.root
        return ""
    full = os.path.abspath(os.path.join(session["cwd"], target))
    if not _inside_root(toolset, full):
        return "error: cd escapes project root: %s" % target
    if not os.path.isdir(full):
        return "error: no such directory: %s" % target
    session["cwd"] = full
    return ""


def _do_export(session, text):
    # export NAME=value, several assignments per line
    parts = text.strip().split()
    for part in parts:
        m = EXPORT_RE.match(part)
        if not m:
            return "error: bad export: %s" % part
        session["env"][m.group(1)] = m.group(2) or ""
    return ""


def register(toolset):
    def shell_session(session="default", command="", timeout="30"):
        name = (session or "default").strip()
        if not SESSION_RE.match(name):
            return ("error: bad session name %r, use letters, digits, "
                    "dash, underscore" % session)
        state = _get(toolset, name)
        cmd = (command or "").strip()
        if not cmd:
            rel = os.path.relpath(state["cwd"], toolset.root)
            keys = ", ".join(sorted(state["env"])) or "(none)"
            return ("session %s\ncwd: %s\nenv: %s"
                    % (name, rel, keys))
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
            return "declined: the command was not run"
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 30
        timeout = min(max(timeout, 1), 300)
        # split on && so cd and export persist between segments
        parts = [p.strip() for p in cmd.split("&&")]
        outputs = []
        for part in parts:
            if not part:
                continue
            if part == "cd" or part.startswith("cd "):
                err = _do_cd(toolset, state, part[2:].strip())
                if err:
                    return err
                continue
            if part == "export" or part.startswith("export "):
                err = _do_export(state, part[6:].strip())
                if err:
                    return err
                continue
            if part.startswith("unset "):
                for var in part[6:].split():
                    state["env"].pop(var, None)
                continue
            m = ASSIGN_RE.match(part)
            if m and " " not in part:
                state["env"][m.group(1)] = m.group(2)
                continue
            outputs.append(_run_one(toolset, state, part, timeout))
            if outputs[-1].startswith("error:"):
                break
        return "\n".join(outputs) or "(no output)"

    def shell_kill(session="default"):
        name = (session or "default").strip()
        sessions = _sessions(toolset)
        if name not in sessions:
            return "error: no such session: %s" % name
        del sessions[name]
        return "closed session %s" % name

    toolset.tools["shell_session"] = {
        "description": "run a command in a named persistent shell "
                       "session, cwd and exported env survive",
        "parameters": {"session": "session name, default 'default'",
                       "command": "the command, empty shows status",
                       "timeout": "seconds, default 30"},
        "run": shell_session,
    }
    toolset.tools["shell_kill"] = {
        "description": "close a named persistent shell session",
        "parameters": {"session": "session name, default 'default'"},
        "run": shell_kill,
    }
