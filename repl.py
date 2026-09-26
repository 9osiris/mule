"""interactive loop: type follow-up tasks, slash commands for the rest."""

import os
import re
import subprocess

from cost import cost_for, fmt_cost
from ui import dim, G

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
        if line.strip().startswith("```"):
            buf = [line]
            while True:
                try:
                    more = read_line("... ")
                except (EOFError, KeyboardInterrupt):
                    more = "```"
                buf.append(more)
                if more.strip().endswith("```") and len(buf) > 1:
                    break
            line = "\n".join(buf)
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
