"""interactive loop: type follow-up tasks, slash commands for the rest."""

import os

from cost import cost_for, fmt_cost

HELP_TEXT = """slash commands:
  /help        show this help
  /quit        exit the loop
  /clear       reset the conversation history
  /save NAME   save the session to ~/.mule/sessions/
  /cost        show tokens and spend so far
  /tools       list available tools
  /undo        restore the most recently changed file"""


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
    elif cmd == "tools":
        names = sorted(ctx["tools"].tools)
        write("\n".join("  %s - %s" % (n, ctx["tools"].tools[n]["description"])
                        for n in names))
    elif cmd in commands:
        return ("run", commands[cmd])
    else:
        write("unknown command: /%s (try /help)" % cmd)
    return None


def repl_loop(read_line, write, on_task, on_slash):
    # read_line(prompt) raises EOFError/KeyboardInterrupt to leave
    write("interactive mode. /help for commands, /quit to leave.")
    while True:
        try:
            line = read_line("mule> ")
        except (EOFError, KeyboardInterrupt):
            write("")
            break
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            if on_slash(line) == "quit":
                break
            continue
        on_task(line)
