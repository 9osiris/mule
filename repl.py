"""interactive loop: type follow-up tasks, slash commands for the rest."""

from cost import cost_for, fmt_cost

HELP_TEXT = """slash commands:
  /help        show this help
  /quit        exit the loop
  /clear       reset the conversation history
  /save NAME   save the session to ~/.mule/sessions/
  /cost        show tokens and spend so far
  /undo        restore the most recently changed file"""


def parse_slash(line):
    # "/save foo bar" -> ("save", "foo bar"), "/quit" -> ("quit", "")
    parts = line[1:].split(None, 1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def handle_slash(line, ctx):
    # ctx: write, tools, totals, model, save_fn
    # returns "quit" to leave, "clear" to reset history, else None
    write = ctx["write"]
    cmd, arg = parse_slash(line)
    if cmd == "help":
        write(HELP_TEXT)
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
