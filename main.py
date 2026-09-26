import argparse
import json
import os
import sys

from agent import run, last_answer, load_system_prompt, plan_and_approve
from client import ChatClient
from config import load_config
from cost import cost_for, fmt_cost
from repl import repl_loop, handle_slash, load_commands
from sessions import save_session, load_session, list_sessions, auto_name
from tools import ToolSet


def _num(env_raw, cfg_raw, default, cast):
    # env beats config beats default, junk falls through to default
    for raw in (env_raw, cfg_raw):
        if raw is None:
            continue
        try:
            return cast(raw)
        except (TypeError, ValueError):
            continue
    return default


def build_parser(cfg):
    p = argparse.ArgumentParser(
        description="a minimal coding agent for any openai-compatible api")
    p.add_argument("task", nargs="?", help="what to do, or read from stdin")
    p.add_argument("--model",
                   default=os.environ.get("MULE_MODEL",
                                          cfg.get("model", "gpt-4o-mini")))
    p.add_argument("--base-url",
                   default=os.environ.get("MULE_BASE_URL",
                                          cfg.get("base_url",
                                                  "https://api.openai.com/v1")))
    p.add_argument("--api-key",
                   default=os.environ.get("OPENAI_API_KEY",
                                          cfg.get("api_key", "")))
    p.add_argument("--root", default=cfg.get("root", "."),
                   help="project dir the agent works in")
    p.add_argument("--max-steps", type=int,
                   default=_num(os.environ.get("MULE_MAX_STEPS"),
                                cfg.get("max_steps"), 25, int))
    p.add_argument("--timeout", type=float,
                   default=_num(os.environ.get("MULE_TIMEOUT"),
                                cfg.get("timeout"), 120, float),
                   help="api request timeout in seconds")
    p.add_argument("--retries", type=int,
                   default=_num(os.environ.get("MULE_RETRIES"),
                                cfg.get("retries"), 3, int),
                   help="retries on 429/5xx with exponential backoff")
    p.add_argument("--max-cost", type=float, default=None, metavar="DOLLARS",
                   help="stop the agent when session cost exceeds this")
    p.add_argument("--system-prompt", default=None)
    p.add_argument("--quiet", action="store_true", help="only print the final answer")
    p.add_argument("--no-stream", action="store_true",
                   help="wait for the full response instead of streaming")
    p.add_argument("--save", nargs="?", const="auto", default=None,
                   help="save the conversation to a session file "
                        "(auto-names if no name given)")
    p.add_argument("--resume", default=None, metavar="NAME",
                   help="resume a saved session, then continue with the task")
    p.add_argument("--list-sessions", action="store_true",
                   help="list saved sessions and exit")
    p.add_argument("--ask", action="store_true",
                   help="ask for confirmation before shell commands and file writes")
    p.add_argument("--undo", action="store_true",
                   help="restore the most recently changed file and exit")
    p.add_argument("--interactive", action="store_true",
                   help="prompt loop for follow-up tasks")
    p.add_argument("--plan", action="store_true",
                   help="write a plan and get your approval before running tools")
    p.add_argument("--context-budget", type=int,
                   default=_num(os.environ.get("MULE_CONTEXT_BUDGET"),
                                cfg.get("context_budget"), 100000, int),
                   help="compact history past this many chars (default: 100000)")
    p.add_argument("--export", default=None, metavar="PATH",
                   help="write the session transcript to a markdown file")
    p.add_argument("--checkpoint", default=None, metavar="NAME",
                   help="tar the project root to ~/.mule/checkpoints/ before the run")
    p.add_argument("--restore", default=None, metavar="NAME",
                   help="restore a checkpoint and exit")
    p.add_argument("--json", action="store_true",
                   help="print the result as json for scripting")
    p.add_argument("--dry-run", action="store_true",
                   help="show planned tool calls without executing them")
    p.add_argument("--system", default=None, metavar="TEXT",
                   help="replace the system prompt with this text")
    p.add_argument("--append-system", default=None, metavar="TEXT",
                   help="append this text to the system prompt")
    p.add_argument("--no-color", action="store_true",
                   help="disable ansi color output")
    return p


def parse_args(argv=None):
    cfg = load_config()
    return build_parser(cfg).parse_args(argv)


SUBCOMMANDS = ("init", "config", "doctor", "completion", "models")


def run_subcommand(name, rest):
    # mule <subcommand>: init/config/doctor/completion/models
    if name == "init":
        from scaffold import init_project
        force = "--force" in rest
        target = next((a for a in rest if not a.startswith("-")), ".")
        try:
            created = init_project(target, force=force)
        except FileExistsError as e:
            print("error: %s" % e, file=sys.stderr)
            return 1
        for path in created:
            print("created %s" % path)
        return 0
    if name == "config":
        return cmd_config(rest)
    if name == "doctor":
        from doctor import run_doctor
        root = "."
        if "--root" in rest:
            i = rest.index("--root")
            if i + 1 < len(rest):
                root = rest[i + 1]
        return run_doctor(root)
    if name == "completion":
        from complete import completion_script
        from cost import PRICING
        if not rest or rest[0] not in ("bash", "zsh", "fish"):
            print("usage: mule completion bash|zsh|fish", file=sys.stderr)
            return 2
        parser = build_parser({})
        flags = sorted({o for a in parser._actions
                        for o in a.option_strings
                        if o.startswith("--")})
        print(completion_script(rest[0], flags, sorted(PRICING)), end="")
        return 0
    print("unknown subcommand: %s" % name, file=sys.stderr)
    return 2


def cmd_config(rest):
    # mule config [--global] get/set/unset/list
    from config import config_get, config_set, config_unset, config_list
    global_ = "--global" in rest
    args = [a for a in rest if a != "--global"]
    if not args or args[0] not in ("get", "set", "unset", "list"):
        print("usage: mule config [--global] get KEY | set KEY VALUE "
              "| unset KEY | list", file=sys.stderr)
        return 2
    action = args[0]
    try:
        if action == "list":
            data = config_list(global_)
            for k in sorted(data):
                print("%s=%s" % (k, json.dumps(data[k])))
        elif action == "get":
            if len(args) < 2:
                print("usage: mule config get KEY", file=sys.stderr)
                return 2
            print(json.dumps(config_get(args[1], global_)))
        elif action == "set":
            if len(args) < 3:
                print("usage: mule config set KEY VALUE", file=sys.stderr)
                return 2
            value = config_set(args[1], " ".join(args[2:]), global_)
            print("%s=%s" % (args[1], json.dumps(value)))
        elif action == "unset":
            if len(args) < 2:
                print("usage: mule config unset KEY", file=sys.stderr)
                return 2
            config_unset(args[1], global_)
            print("unset %s" % args[1])
    except KeyError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


def ask_cmd(command):
    # the human-in-the-loop gate for --ask
    print("run this? %s" % command)
    ans = input("[y/N] ").strip().lower()
    return ans in ("y", "yes")


def ask_plan(plan):
    # the human-in-the-loop gate for --plan
    print("plan:")
    print(plan)
    ans = input("[y]es / [n]o / [r]evise: ").strip().lower()
    if ans in ("r", "revise"):
        return "r"
    return "y" if ans in ("y", "yes") else "n"


def make_chat_fn(args, client):
    def chat_fn(messages, tools):
        # stream tokens live unless --no-stream was passed
        if args.no_stream:
            return client.chat(messages, tools)
        printed = []

        def on_token(t):
            if args.quiet:
                return
            print(t, end="", flush=True)
            printed.append(t)

        reply = client.chat_stream(messages, tools, on_token=on_token)
        if printed and not args.quiet:
            print()
        return reply
    return chat_fn


def make_show(args):
    def show(step, calls):
        if args.quiet:
            return
        for c in calls:
            a = json.loads(c["function"].get("arguments") or "{}")
            summary = " ".join("%s=%s" % (k, str(v)[:60]) for k, v in a.items())
            print("$ %s %s" % (c["function"]["name"], summary))
    return show


def make_todos(args):
    def show_todos(line):
        if not args.quiet:
            print(line)
    return show_todos


def make_track(args, totals):
    def track(step, usage):
        # returns True when the cost budget is blown, stopping the loop
        if usage:
            pin = usage.get("prompt_tokens", 0)
            pout = usage.get("completion_tokens", 0)
            totals["in"] += pin
            totals["out"] += pout
            if not args.quiet:
                print("  [step %d: %s in / %s out, %s]" % (
                    step, "{:,}".format(pin), "{:,}".format(pout),
                    fmt_cost(cost_for(args.model, pin, pout))))
        if args.max_cost is not None:
            spent = cost_for(args.model, totals["in"], totals["out"])
            if spent is not None and spent > args.max_cost:
                return True
        return False
    return track


def run_interactive(args, tools, system, messages, task,
                    chat_fn, show, track, totals, todos):
    # prompt loop: each line is a task, history carries over
    box = {"messages": messages}
    commands = load_commands(
        os.path.join(os.path.abspath(args.root), ".mule", "commands"))

    def save_fn(name):
        return save_session(name, box["messages"])

    ctx = {"write": print, "tools": tools, "totals": totals,
           "model": args.model, "save_fn": save_fn,
           "commands": commands}

    def on_slash(line):
        action = handle_slash(line, ctx)
        if action == "clear":
            box["messages"] = [{"role": "system", "content": system}]
            print("history cleared")
        elif isinstance(action, tuple) and action[0] == "run":
            # a custom command becomes the next task
            on_task(action[1])
            return None
        return action

    def on_task(line):
        try:
            box["messages"] = run(line, chat_fn, tools,
                                  system_prompt=system,
                                  max_steps=args.max_steps,
                                  on_step=show,
                                  messages=box["messages"],
                                  usage_cb=track,
                                  on_todos=todos,
                                  context_budget=args.context_budget)
        except RuntimeError as e:
            print("error: %s" % e)
            return
        if not args.quiet:
            print("---")
        print(last_answer(box["messages"]))

    if task:
        # a task on the command line runs first, then the loop takes over
        on_task(task)
    repl_loop(input, print, on_task, on_slash)
    if args.export:
        from sessions import export_session
        export_session(args.export, box["messages"],
                       cost_line="tokens: %s in / %s out, cost %s" % (
                           "{:,}".format(totals["in"]),
                           "{:,}".format(totals["out"]),
                           fmt_cost(cost_for(args.model, totals["in"],
                                             totals["out"]))))
        print("exported: %s" % args.export)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return run_subcommand(argv[0], argv[1:])
    args = parse_args(argv)

    if args.list_sessions:
        for name in list_sessions():
            print(name)
        return 0

    tools = ToolSet(args.root, confirm=ask_cmd if args.ask else None)
    if args.undo:
        print(tools.undo_last())
        return 0
    if args.restore:
        from checkpoints import restore_checkpoint
        try:
            path = restore_checkpoint(args.root, args.restore)
        except ValueError as e:
            print("error: %s" % e, file=sys.stderr)
            return 1
        print("restored checkpoint: %s" % path)
        return 0
    if args.checkpoint:
        from checkpoints import save_checkpoint
        path = save_checkpoint(args.root, args.checkpoint)
        if not args.quiet:
            print("checkpoint saved: %s" % path)

    task = args.task
    if not task and not args.interactive and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    if not task and not args.resume and not args.interactive:
        print("give it a task, as an argument or on stdin", file=sys.stderr)
        return 2
    if not args.api_key:
        print("set OPENAI_API_KEY or pass --api-key", file=sys.stderr)
        return 2

    client = ChatClient(args.base_url, args.api_key, args.model,
                        timeout=args.timeout, retries=args.retries)
    system = load_system_prompt(args.system_prompt)

    messages = None
    if args.resume:
        try:
            messages = load_session(args.resume)
        except ValueError as e:
            print("error: %s" % e, file=sys.stderr)
            return 1
    if args.interactive and messages is None:
        messages = [{"role": "system", "content": system}]

    chat_fn = make_chat_fn(args, client)
    show = make_show(args)
    totals = {"in": 0, "out": 0}
    track = make_track(args, totals)
    todos = make_todos(args)

    if args.interactive:
        return run_interactive(args, tools, system, messages, task,
                               chat_fn, show, track, totals, todos)

    run_task, run_messages = task, messages
    if args.plan:
        run_messages, approved = plan_and_approve(
            task, chat_fn, ask_plan, system, messages=messages)
        if not approved:
            print("plan rejected, stopping")
            return 0
        run_task = None  # the task is already in the history

    try:
        messages = run(run_task, chat_fn, tools,
                       system_prompt=system,
                       max_steps=args.max_steps,
                       on_step=show,
                       messages=run_messages,
                       usage_cb=track,
                       on_todos=todos,
                       context_budget=args.context_budget)
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1

    if args.save:
        name = auto_name() if args.save == "auto" else args.save
        path = save_session(name, messages)
        if not args.quiet:
            print("saved session: %s" % path)

    cost_line = "tokens: %s in / %s out, cost %s" % (
        "{:,}".format(totals["in"]), "{:,}".format(totals["out"]),
        fmt_cost(cost_for(args.model, totals["in"], totals["out"])))
    if args.export:
        from sessions import export_session
        export_session(args.export, messages, cost_line=cost_line)
        if not args.quiet:
            print("exported: %s" % args.export)

    if not args.quiet:
        print("---")
        print(cost_line)
    print(last_answer(messages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
