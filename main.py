import argparse
import json
import os
import sys

from agent import run, last_answer, load_system_prompt
from client import ChatClient
from config import load_config
from cost import cost_for, fmt_cost
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


def parse_args(argv=None):
    cfg = load_config()
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
                   help="ask for confirmation before each shell command")
    return p.parse_args(argv)


def ask_cmd(command):
    # the human-in-the-loop gate for --ask
    print("run this? %s" % command)
    ans = input("[y/N] ").strip().lower()
    return ans in ("y", "yes")


def main(argv=None):
    args = parse_args(argv)

    if args.list_sessions:
        for name in list_sessions():
            print(name)
        return 0

    task = args.task
    if not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    if not task and not args.resume:
        print("give it a task, as an argument or on stdin", file=sys.stderr)
        return 2
    if not args.api_key:
        print("set OPENAI_API_KEY or pass --api-key", file=sys.stderr)
        return 2

    tools = ToolSet(args.root, confirm=ask_cmd if args.ask else None)
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

    def show(step, calls):
        if args.quiet:
            return
        for c in calls:
            a = json.loads(c["function"].get("arguments") or "{}")
            summary = " ".join("%s=%s" % (k, str(v)[:60]) for k, v in a.items())
            print("$ %s %s" % (c["function"]["name"], summary))

    totals = {"in": 0, "out": 0}

    def track(step, usage):
        if not usage or args.quiet:
            return
        pin = usage.get("prompt_tokens", 0)
        pout = usage.get("completion_tokens", 0)
        totals["in"] += pin
        totals["out"] += pout
        print("  [step %d: %s in / %s out, %s]" % (
            step, "{:,}".format(pin), "{:,}".format(pout),
            fmt_cost(cost_for(args.model, pin, pout))))

    try:
        messages = run(task, chat_fn, tools,
                       system_prompt=system,
                       max_steps=args.max_steps,
                       on_step=show,
                       messages=messages,
                       usage_cb=track)
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1

    if args.save:
        name = auto_name() if args.save == "auto" else args.save
        path = save_session(name, messages)
        if not args.quiet:
            print("saved session: %s" % path)

    if not args.quiet:
        print("---")
        print("tokens: %s in / %s out, cost %s" % (
            "{:,}".format(totals["in"]), "{:,}".format(totals["out"]),
            fmt_cost(cost_for(args.model, totals["in"], totals["out"]))))
    print(last_answer(messages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
