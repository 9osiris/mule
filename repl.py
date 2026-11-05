"""interactive loop: type follow-up tasks, slash commands for the rest."""

import os
import re
import subprocess

from cost import cost_for, fmt_cost
from ui import dim, G

# persistent repl history, capped so the file stays small
HISTORY_PATH = os.path.expanduser("~/.mule/repl_history")
HISTORY_MAX_LINES = 1000


def append_history(line):
    # best effort append, never raise into the repl
    if line is None:
        return
    text = str(line)
    if not text.strip():
        return
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH) or ".", exist_ok=True)
        with open(HISTORY_PATH, "a") as f:
            f.write(text.rstrip("\n") + "\n")
        with open(HISTORY_PATH) as f:
            lines = f.readlines()
        if len(lines) > HISTORY_MAX_LINES:
            with open(HISTORY_PATH, "w") as f:
                f.writelines(lines[-HISTORY_MAX_LINES:])
    except OSError:
        pass


def load_history():
    # saved lines oldest first, empty list when nothing is there
    try:
        with open(HISTORY_PATH) as f:
            return [l.rstrip("\n") for l in f]
    except OSError:
        return []


def needs_continuation(text):
    # unclosed ``` fence or a trailing backslash means more input
    if text.count("```") % 2 == 1:
        return True
    if text.rstrip().endswith("\\"):
        return True
    return False


def join_continued(lines):
    # backslash-newline joins with nothing, fences keep newlines
    out = "\n".join(lines)
    return out.replace("\\\n", "")

HELP_TEXT = """slash commands:
  /help        show this help
  /quit        exit the loop
  /clear       reset the conversation history
  /save NAME   save the session to ~/.mule/sessions/
  /cost        show tokens and spend so far
  /tools       list available tools
  /tools off NAME   disable a tool for this session
  /tools on NAME    re-enable a tool
  /model [NAME]    show or switch the model
  /retry       run the last task again
  /compress    summarize history into a short recap
  /undo        restore the most recently changed file
  /bug [TEXT]  print a prefilled github issue url
  /sessions    list saved sessions
  /branch NAME --from PARENT   fork a saved session
  /export NAME md|html   export a session transcript
  /persona [NAME]   list personas or switch the system prompt
extras:
  !CMD         run a shell command directly, no agent involved
  @PATH        attach a file's contents to your task
tip: wrap input in ``` blocks for multiline tasks"""


# @path or @"my file.py": pulled from the project root, dropped
# into a code fence before the task runs. missing files and
# paths escaping the root are left alone.
_AT_RE = re.compile(r"(?<!\S)@(\"[^\"\n]+\"|'[^'\n]+'|[\w.\-\\/]+)")


def expand_at_refs(line, root):
    def _put(m):
        path = m.group(1).strip("\"'")
        full = os.path.normpath(os.path.join(root, path))
        if not full.startswith(os.path.abspath(root) + os.sep):
            return m.group(0)
        if not os.path.isfile(full):
            return m.group(0)
        try:
            with open(full, errors="replace") as f:
                content = f.read()
        except OSError:
            return m.group(0)
        if len(content) > 30000:
            content = content[:30000] + "\n... (truncated)"
        return "\n```%s\n%s\n```\n" % (path, content)
    return _AT_RE.sub(_put, line)


def run_shell_line(cmd, write):
    # "!ls -la" from the repl: run it now, show the output,
    # stay in the loop. the agent never sees it.
    cmd = cmd.strip()
    if not cmd:
        write("usage: !COMMAND")
        return
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=180)
    except Exception as e:
        write("error: %s" % e)
        return
    out = ((p.stdout or "") + (p.stderr or "")).rstrip()
    if out:
        write(out)
    if p.returncode:
        write(dim("exit %d" % p.returncode))


def load_commands(commands_dir):
    # .mule/commands/*.md become /name commands, content is the text
    cmds = {}
    if not os.path.isdir(commands_dir):
        return cmds
    for fname in sorted(os.listdir(commands_dir)):
        if not fname.endswith(".md"):
            continue
        name = fname[:-3].strip().lower()
        if not name:
            continue
        with open(os.path.join(commands_dir, fname)) as f:
            cmds[name] = f.read().strip()
    return cmds


def parse_slash(line):
    # "/save foo bar" -> ("save", "foo bar"), "/quit" -> ("quit", "")
    parts = line[1:].split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def _export_via_report(report, name, fmt, out_dir=None):
    # session_report renders text: export_markdown / export_html
    # return a string, which we write to a file and report.
    # also accept a generic export(name, fmt) returning a path.
    fn_name = "export_markdown" if fmt == "md" else "export_html"
    fn = getattr(report, fn_name, None)
    if callable(fn):
        text = fn(name)
        path = os.path.join(out_dir or ".", "%s.%s" % (name, fmt))
        with open(path, "w") as f:
            f.write(text)
        return path
    for fn_name in ("export", "export_session", "render"):
        fn = getattr(report, fn_name, None)
        if callable(fn):
            out = fn(name, fmt)
            if isinstance(out, str) and "\n" in out:
                path = os.path.join(out_dir or ".",
                                    "%s.%s" % (name, fmt))
                with open(path, "w") as f:
                    f.write(out)
                return path
            return out
    raise ValueError("session_report has no export function")


def handle_slash(line, ctx):
    # ctx: write, tools, totals, model, save_fn, commands
    # returns "quit" to leave, "clear" to reset history,
    # ("run", text) to run a custom command as the next task, else None
    write = ctx["write"]
    commands = ctx.get("commands") or {}
    cmd, arg = parse_slash(line)
    if cmd == "help":
        text = HELP_TEXT
        if commands:
            text += ("\ncustom:\n" + "\n".join(
                "  /%s" % n for n in sorted(commands)))
        write(text)
    elif cmd == "quit":
        return "quit"
    elif cmd == "clear":
        return "clear"
    elif cmd == "save":
        if not arg:
            write("usage: /save NAME")
        else:
            write("saved session: %s" % ctx["save_fn"](arg))
    elif cmd == "cost":
        t = ctx["totals"]
        write("tokens: %s in / %s out, cost %s" % (
            "{:,}".format(t["in"]), "{:,}".format(t["out"]),
            fmt_cost(cost_for(ctx["model"], t["in"], t["out"]))))
    elif cmd == "undo":
        write(ctx["tools"].undo_last())
    elif cmd == "model":
        if arg and ctx.get("set_model"):
            ctx["set_model"](arg)
            write("model: %s" % arg)
        elif arg:
            write("model switching is not available here")
        else:
            write("model: %s" % ctx.get("model", "?"))
    elif cmd == "retry":
        last = ctx.get("last_task")
        if last:
            return ("run", last)
        write("nothing to retry yet")
    elif cmd == "compress":
        return "compress"
    elif cmd == "bug":
        from urllib.parse import quote
        title = arg or "bug report"
        body = ("mule version: %s\n\nwhat happened:\n\n"
                "what you expected:\n\nsteps to reproduce:\n"
                % ctx.get("version", "?"))
        url = ("https://github.com/9osiris/mule/issues/new"
               "?title=%s&body=%s" % (quote(title), quote(body)))
        write("file it here:\n%s" % url)
    elif cmd == "tools":
        if arg:
            # /tools off NAME | /tools on NAME
            bits = arg.split(None, 1)
            action = bits[0].lower() if bits else ""
            name = bits[1] if len(bits) > 1 else ""
            if action == "off" and name:
                ctx["tools"].disable_tool(name)
                write("disabled: %s" % name)
            elif action == "on" and name:
                ctx["tools"].enable_tool(name)
                write("enabled: %s" % name)
            else:
                write("usage: /tools [off NAME | on NAME]")
        else:
            names = sorted(n for n in ctx["tools"].tools
                           if n not in ctx["tools"].disabled)
            write("\n".join("  %s - %s" % (n, ctx["tools"].tools[n]["description"])
                            for n in names))
    elif cmd == "sessions":
        try:
            import sessions as _sessions
            names = _sessions.list_sessions()
        except OSError:
            names = []
        if not names:
            write("(no saved sessions)")
        else:
            rows = []
            for n in names:
                try:
                    parent = _sessions.branch_parent(n)
                except Exception:
                    parent = None
                rows.append("  %s%s" % (n, " (branch of %s)" % parent
                                        if parent else ""))
            write("\n".join(rows))
    elif cmd == "branch":
        bits = arg.split()
        if len(bits) < 2 or "--from" not in bits:
            write("usage: /branch NAME --from PARENT")
        else:
            name = bits[0]
            parent = bits[bits.index("--from") + 1] if (
                bits.index("--from") + 1 < len(bits)) else ""
            if not parent:
                write("usage: /branch NAME --from PARENT")
            else:
                try:
                    import sessions as _sessions
                    path = _sessions.branch_session(name, parent)
                    write("branched: %s" % path)
                except ValueError as e:
                    write("error: %s" % e)
                except OSError as e:
                    write("error: %s" % e)
    elif cmd == "export":
        bits = arg.split()
        if len(bits) < 2:
            write("usage: /export NAME md|html")
        else:
            name, fmt = bits[0], bits[1].lower()
            if fmt not in ("md", "html"):
                write("usage: /export NAME md|html")
            else:
                try:
                    import session_report as _report
                except ImportError:
                    write("session export is not available: "
                          "session_report module not found")
                else:
                    try:
                        out = _export_via_report(
                            _report, name, fmt,
                            out_dir=ctx.get("export_dir"))
                    except ValueError as e:
                        write("error: %s" % e)
                    except Exception as e:
                        write("error: %s" % e)
                    else:
                        write("exported: %s" % out if out else
                              "exported session: %s" % name)
    elif cmd == "persona":
        try:
            import personas as _personas
        except ImportError:
            write("personas not available")
        else:
            if not arg:
                try:
                    names = _personas.list_personas()
                except Exception as e:
                    write("error: %s" % e)
                else:
                    if names:
                        write("\n".join("  %s" % n for n in names))
                    else:
                        write("(no personas)")
            else:
                try:
                    text = _personas.load_persona(arg)
                except ValueError as e:
                    write("error: %s" % e)
                except Exception as e:
                    write("error: %s" % e)
                else:
                    ctx["persona"] = arg
                    ctx["persona_text"] = text
                    if ctx.get("set_persona"):
                        ctx["set_persona"](arg, text)
                    write("persona: %s" % arg)
    elif cmd in commands:
        return ("run", commands[cmd])
    else:
        write("unknown command: /%s (try /help)" % cmd)
    return None


def repl_loop(read_line, write, on_task, on_slash, prompt=None,
              root=None):
    # read_line(prompt) raises EOFError/KeyboardInterrupt to leave.
    # the prompt glyph follows the terminal: pretty unicode when
    # it can render, plain > on legacy consoles.
    if prompt is None:
        prompt = G["prompt"] + " "
    # a line starting with ``` opens a multiline block: everything
    # until the closing ``` becomes one task.
    # a line starting with ! runs a shell command directly.
    # @path in a task pulls that file into a code fence first.
    write("interactive mode. /help for commands, /quit to leave. "
          "!cmd runs shell, @file attaches a file.")
    while True:
        try:
            line = read_line(prompt)
        except (EOFError, KeyboardInterrupt):
            write("")
            break
        append_history(line)
        if line.strip().startswith("```"):
            buf = [line]
            while True:
                try:
                    more = read_line("... ")
                except (EOFError, KeyboardInterrupt):
                    more = "```"
                append_history(more)
                buf.append(more)
                if more.strip().endswith("```") and len(buf) > 1:
                    break
            line = "\n".join(buf)
        elif needs_continuation(line):
            # trailing backslash or an unclosed fence opened
            # mid-line: keep reading until the input closes
            buf = [line]
            while needs_continuation("\n".join(buf)):
                try:
                    more = read_line("... ")
                except (EOFError, KeyboardInterrupt):
                    break
                append_history(more)
                buf.append(more)
            line = join_continued(buf)
        line = line.strip()
        if not line:
            continue
        if line.startswith("!"):
            run_shell_line(line[1:], write)
            continue
        if line.startswith("/"):
            if on_slash(line) == "quit":
                break
            continue
        if root and "@" in line:
            line = expand_at_refs(line, root)
        on_task(line)


def compress_history(chat_fn, messages, track=None):
    # one model call summarizes the conversation, history becomes
    # system prompt plus the recap. keeps long sessions usable.
    reply = chat_fn(messages + [{
        "role": "user",
        "content": "summarize this conversation in under 400 words as "
                   "notes for your future self. keep every key fact, "
                   "decision, file change, and open task.",
    }], [])
    if track:
        track(0, reply.get("usage"))
    summary = reply.get("content") or "(empty summary)"
    system = [m for m in messages if m.get("role") == "system"]
    return system + [{"role": "user",
                      "content": "compressed recap of our work so far:\n"
                                 + summary}]
