import argparse
import json
import os
import sys

from agent import run, last_answer, load_system_prompt, plan_and_approve
from client import ChatClient
from config import load_config, load_profile
from cost import cost_for, fmt_cost
from repl import repl_loop, handle_slash, load_commands
from sessions import save_session, load_session, list_sessions, auto_name
from tools import ToolSet
from ui import init_color, red, yellow


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
    p.add_argument("--print", dest="print_mode", action="store_true",
                   help="print only the final answer, no confirmations, "
                        "for scripting")
    p.add_argument("--output", metavar="FILE",
                   help="write the final answer to FILE too")
    return p


def parse_args(argv=None):
    cfg = load_config()
    # --profile has to win before the real parse, so flags can beat it
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--profile", default=None)
    known, _ = pre.parse_known_args(argv)
    if known.profile:
        try:
            cfg.update(load_profile(known.profile))
        except ValueError as e:
            pre.error(str(e))
    parser = build_parser(cfg)
    parser.add_argument("--profile", default=known.profile,
                        help="use a saved profile from ~/.mule/profiles/")
    return parser.parse_args(argv)


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
            print(red("error: %s" % e), file=sys.stderr)
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
    if name == "models":
        from cost import PRICING
        print("%-14s %10s %10s" % ("model", "in $/1M", "out $/1M"))
        for model in sorted(PRICING):
            pin, pout = PRICING[model]
            print("%-14s %10.2f %10.2f" % (model, pin, pout))
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
        print(red("error: %s" % e), file=sys.stderr)
        return 1
    return 0


def ask_cmd(command):
    # the human-in-the-loop gate for --ask
    print(yellow("run this? %s" % command))
    ans = input("[y/N] ").strip().lower()
    return ans in ("y", "yes")


def ask_user_cli(question, options):
    # the human-in-the-loop gate for the ask_user tool
    print(yellow("question: %s" % question))
    for i, o in enumerate(options, 1):
        print("  %d. %s" % (i, o))
    ans = input("pick 1-%d or type your answer: " % len(options)).strip()
    if ans.isdigit() and 1 <= int(ans) <= len(options):
        return options[int(ans) - 1]
    return ans or options[0]


def ask_plan(plan):
    # the human-in-the-loop gate for --plan
    print(yellow("plan:"))
    print(plan)
    ans = input("[y]es / [n]o / [r]evise: ").strip().lower()
    if ans in ("r", "revise"):
        return "r"
    return "y" if ans in ("y", "yes") else "n"


def make_chat_fn(args, client):
    def chat_fn(messages, tools):
        # stream tokens live unless --no-stream was passed.
        # --print stays silent, it only wants the final answer.
        if args.no_stream or getattr(args, "print_mode", False):
            return client.chat(messages, tools)
        printed = []

        def on_token(t):
            if args.quiet or args.json:
                return
            print(t, end="", flush=True)
            printed.append(t)

        reply = client.chat_stream(messages, tools, on_token=on_token)
        if printed and not args.quiet and not args.json:
            print()
        return reply
    return chat_fn


def say(args, msg):
    # info lines go to stderr in --json mode, keeping stdout pure json
    if args.json:
        print(msg, file=sys.stderr)
    else:
        print(msg)


def build_result(args, messages, totals):
    # the machine-readable result for --json
    return {
        "answer": last_answer(messages),
        "steps": sum(1 for m in messages
                     if m.get("role") == "assistant" and m.get("tool_calls")),
        "model": args.model,
        "tokens_in": totals["in"],
        "tokens_out": totals["out"],
        "cost": cost_for(args.model, totals["in"], totals["out"]),
    }


def make_show(args):
    def show(step, calls):
        if args.quiet or args.json or args.print_mode:
            return
        for c in calls:
            a = json.loads(c["function"].get("arguments") or "{}")
            summary = " ".join("%s=%s" % (k, str(v)[:60]) for k, v in a.items())
            print("$ %s %s" % (c["function"]["name"], summary))
    return show


def make_todos(args):
    def show_todos(line):
        if not args.quiet and not args.json and not args.print_mode:
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
            if not args.quiet and not args.json and not args.print_mode:
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
                                  context_budget=args.context_budget,
                                  dry_run=args.dry_run)
        except RuntimeError as e:
            print(red("error: %s" % e))
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


def resolve_system(args):
    # --system replaces the prompt, --system-prompt loads it from a
    # file, otherwise prompt.md or the builtin. --append-system tacks
    # extra instructions onto whichever one won. skills from
    # .mule/skills/ go on the end.
    if args.system:
        system = args.system
    else:
        system = load_system_prompt(args.system_prompt)
    if args.append_system:
        system = system.rstrip() + "\n\n" + args.append_system
    from skills import load_skills, skills_prompt
    block = skills_prompt(load_skills(
        os.path.join(os.path.abspath(args.root), ".mule", "skills")))
    if block:
        system = system.rstrip() + "\n\n" + block
    return system


def resolve_task(args, stdin):
    # the task comes from the arg, from "-" (explicit stdin), or from
    # a pipe when stdin is not a tty
    task = args.task
    if task == "-" or (not task and not args.interactive
                       and not stdin.isatty()):
        task = stdin.read().strip()
    return task


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return run_subcommand(argv[0], argv[1:])
    args = parse_args(argv)
    init_color(args.no_color)

    if args.list_sessions:
        for name in list_sessions():
            print(name)
        return 0

    tools = ToolSet(args.root,
                    confirm=ask_cmd if (args.ask and not args.print_mode)
                    else None,
                    ask=ask_user_cli
                    if ((args.interactive or args.ask)
                        and not args.print_mode) else None)
    from plugins import load_plugins
    plugin_tools, plugin_errors = load_plugins()
    for t in plugin_tools:
        if t["name"] in tools.tools:
            say(args, "warning: plugin tool %s shadows a builtin, "
                      "skipping" % t["name"])
            continue
        tools.tools[t["name"]] = t
    for e in plugin_errors:
        say(args, "warning: %s" % e)
    if args.undo:
        print(tools.undo_last())
        return 0
    if args.restore:
        from checkpoints import restore_checkpoint
        try:
            path = restore_checkpoint(args.root, args.restore)
        except ValueError as e:
            print(red("error: %s" % e), file=sys.stderr)
            return 1
        print("restored checkpoint: %s" % path)
        return 0
    if args.checkpoint:
        from checkpoints import save_checkpoint
        path = save_checkpoint(args.root, args.checkpoint)
        if not args.quiet:
            say(args, "checkpoint saved: %s" % path)

    task = resolve_task(args, sys.stdin)
    if not task and not args.resume and not args.interactive:
        print(red("give it a task, as an argument or on stdin"),
              file=sys.stderr)
        return 2
    if not args.api_key:
        print(red("set OPENAI_API_KEY or pass --api-key"), file=sys.stderr)
        return 2

    client = ChatClient(args.base_url, args.api_key, args.model,
                        timeout=args.timeout, retries=args.retries)
    tools.make_chat = lambda: make_chat_fn(args, client)
    system = resolve_system(args)

    messages = None
    if args.resume:
        try:
            messages = load_session(args.resume)
        except ValueError as e:
            print(red("error: %s" % e), file=sys.stderr)
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
        if args.dry_run and not args.quiet and not args.json:
            print("dry run: tools will not be executed")
        messages = run(run_task, chat_fn, tools,
                       system_prompt=system,
                       max_steps=args.max_steps,
                       on_step=show,
                       messages=run_messages,
                       usage_cb=track,
                       on_todos=todos,
                       context_budget=args.context_budget,
                       dry_run=args.dry_run)
    except RuntimeError as e:
        print(red("error: %s" % e), file=sys.stderr)
        return 1

    if args.save:
        name = auto_name() if args.save == "auto" else args.save
        path = save_session(name, messages)
        if not args.quiet:
            say(args, "saved session: %s" % path)

    cost_line = "tokens: %s in / %s out, cost %s" % (
        "{:,}".format(totals["in"]), "{:,}".format(totals["out"]),
        fmt_cost(cost_for(args.model, totals["in"], totals["out"])))
    if args.export:
        from sessions import export_session
        export_session(args.export, messages, cost_line=cost_line)
        if not args.quiet:
            say(args, "exported: %s" % args.export)

    if args.json:
        print(json.dumps(build_result(args, messages, totals), indent=2))
        return 0

    if args.output:
        # write the final answer to a file too, works with --print
        answer = last_answer(messages)
        wrote = False
        try:
            with open(args.output, "w") as f:
                f.write(answer + "\n")
            wrote = True
        except OSError as e:
            say(args, "warning: could not write %s: %s"
                       % (args.output, e))
        if wrote and not args.print_mode:
            print("wrote answer to %s" % args.output)

    if args.print_mode:
        # scripting mode: just the answer, nothing else
        print(last_answer(messages))
        return 0

    if not args.quiet:
        print("---")
        print(cost_line)
    print(last_answer(messages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
