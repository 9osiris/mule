import argparse
import json
import os
import sys

from agent import run, last_answer, load_system_prompt, plan_and_approve, \
    _clean_reply
from client import ChatClient
from config import load_config, load_profile, config_problems

# exit codes: 0 ok, 2 budget hit (or usage error), 3 runtime error,
# 130 user pressed ctrl-c
EXIT_OK, EXIT_BUDGET, EXIT_ERROR, EXIT_CANCELLED = 0, 2, 3, 130

__version__ = "0.9.0"
from cost import cost_for, fmt_cost
from repl import repl_loop, handle_slash, load_commands
from sessions import save_session, load_session, list_sessions, auto_name, \
    latest_session, search_sessions, rename_session, delete_session, \
    session_stats
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
    p.add_argument("--version", action="version",
                   version="mule %s" % __version__)
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
    p.add_argument("--search-sessions", default=None, metavar="QUERY",
                   help="search saved sessions for text and exit")
    p.add_argument("--ask", action="store_true",
                   default=cfg.get("ask", False),
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
    p.add_argument("--temperature", type=float,
                   default=_num(None, cfg.get("temperature"), None, float),
                   help="sampling temperature, lower is more focused")
    p.add_argument("--max-tokens", type=int, default=None,
                   help="cap on completion tokens per request")
    p.add_argument("--seed", type=int, default=None,
                   help="seed for reproducible outputs")
    p.add_argument("--continue", dest="cont", action="store_true",
                   help="pick up the most recent session")
    p.add_argument("--fork", default=None, metavar="NAME",
                   help="branch off a saved session as a new run")
    p.add_argument("--reflect", action="store_true",
                   default=cfg.get("reflect", False),
                   help="critique the final answer and improve it "
                        "before returning")
    p.add_argument("--fallback-model", default=cfg.get("fallback_model"),
                   metavar="MODEL",
                   help="switch to this model if the primary keeps failing")
    p.add_argument("--stop", default=None, metavar="SEQS",
                   help="comma-separated stop sequences for the model")
    p.add_argument("--schema", default=None, metavar="FILE",
                   help="validate the final answer as json against this "
                        "schema file ({\"required\": [...]})")
    p.add_argument("--max-tools", type=int,
                   default=_num(None, cfg.get("max_tools"), None, int),
                   help="stop after this many tool executions")
    p.add_argument("--time-limit", type=float,
                   default=_num(None, cfg.get("time_limit"), None, float),
                   metavar="SECONDS",
                   help="stop the run after this many seconds")
    p.add_argument("--parallel-tools", action="store_true",
                   default=cfg.get("parallel_tools", False),
                   help="run independent tool calls in one turn concurrently")
    p.add_argument("--verbose", action="store_true",
                   default=cfg.get("verbose", False),
                   help="print per-step timing and extra detail")
    p.add_argument("--allow-tools", default=cfg.get("allow_tools"),
                   metavar="NAMES",
                   help="comma-separated tools the model may use, "
                        "everything else is disabled")
    p.add_argument("--deny-tools", default=cfg.get("deny_tools"),
                   metavar="NAMES",
                   help="comma-separated tools to disable")
    p.add_argument("--readonly", action="store_true",
                   default=cfg.get("readonly", False),
                   help="disable file writes and shell commands")
    p.add_argument("--allow-network", dest="allow_network",
                   action="store_true", default=None,
                   help="allow network tools (the default), overrides "
                        "no_network in config")
    p.add_argument("--no-network", dest="no_network",
                   action="store_true",
                   default=cfg.get("no_network", False),
                   help="disable fetch_url, web_search, and http_post")
    p.add_argument("--template", default=None, metavar="NAME",
                   help="run the task through .mule/templates/NAME.md, "
                        "{{task}} becomes your task text")
    p.add_argument("--import", dest="import_", default=None, metavar="FILE",
                   help="start from a markdown or jsonl history file")
    p.add_argument("--log-file", default=cfg.get("log_file"),
                   metavar="FILE",
                   help="append all output to FILE as well as the terminal")
    p.add_argument("--trace", default=cfg.get("trace"), metavar="FILE",
                   help="write raw api request/response pairs to FILE as jsonl")
    p.add_argument("--tool-timeout", type=float,
                   default=cfg.get("tool_timeout"),
                   metavar="SECONDS",
                   help="kill the wait on any single tool call after SECONDS")
    return p


def parse_args(argv=None):
    cfg = load_config()
    for problem in config_problems():
        print("config warning: %s" % problem, file=sys.stderr)
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


SUBCOMMANDS = ("init", "config", "doctor", "completion", "models",
               "sessions", "help", "examples", "demo")


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
            return EXIT_ERROR
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
    if name == "sessions":
        return cmd_sessions(rest)
    if name == "help":
        return cmd_help(rest)
    if name == "examples":
        return cmd_examples()
    if name == "demo":
        return cmd_demo(rest)
    print("unknown subcommand: %s" % name, file=sys.stderr)
    return 2


def _demo_message(body):
    # the fake model: lists the dir once, then reports back
    messages = body.get("messages", [])
    saw_tool_result = any(m.get("role") == "tool" for m in messages)
    if not saw_tool_result:
        return {"role": "assistant", "content": None,
                "tool_calls": [{"id": "demo-1", "type": "function",
                                "function": {"name": "list_dir",
                                             "arguments": "{}"}}]}
    listing = next((m.get("content", "") for m in messages
                    if m.get("role") == "tool"), "")
    n = len([line for line in listing.splitlines() if line.strip()])
    return {"role": "assistant",
            "content": "demo done. the fake model saw %d entries in "
                       "the project root. set OPENAI_API_KEY and run "
                       "mule for real work." % n}


def cmd_demo(rest):
    # try the whole loop with no api key: a fake model on localhost
    # lists the project dir, then writes its summary
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    class DemoHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            data = json.dumps({
                "choices": [{"message": _demo_message(body)}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), DemoHandler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    task = " ".join(rest) or "list the files in this project"
    print("demo mode: fake model on 127.0.0.1:%d" % port)
    try:
        return main(["--base-url", "http://127.0.0.1:%d/v1" % port,
                     "--api-key", "demo", "--model", "demo-model",
                     "--max-steps", "6", "--no-stream", task])
    finally:
        server.shutdown()


EXAMPLES = """examples:
  mule "fix the failing test in test_auth.py"
  mule "add docstrings to agent.py" --ask
  mule "summarize this repo" --readonly --print
  mule --interactive
  mule "refactor main.py" --plan
  mule "write a haiku about git" --model gpt-4o-mini --print
  mule "find dead code" --allow-tools read_file,grep,find
  mule "check the diff" --no-network
  mule --template review "main.py"
  mule sessions stats
  mule config set model gpt-4o"""


def cmd_examples():
    print(EXAMPLES)
    return 0


HELP_TOPICS = {
    "tools": """tools the agent can call:
  read_file, write_file, edit_file, apply_patch   files
  list_dir, tree, find, grep, read_many, file_info  browsing
  run_shell, jobs, job_output, job_kill            shell
  git_status, git_diff, git_log                   git
  fetch_url, web_search, http_post                network
  todo_write, todo_read                           planning
  ask_user, delegate, read_image                   misc
gate them with --allow-tools, --deny-tools,
--readonly, or --no-network.""",
    "config": """config lives in ./mule.json, falling back to
~/.config/mule/mule.json. local beats home, env beats
config, flags beat everything.
  mule config list            show every key
  mule config get KEY         read one key
  mule config set KEY VALUE   write one key
  mule config unset KEY       remove one key
keys: model, api_key, base_url, max_steps, ask,
reflect, readonly, no_network, allow_tools...""",
    "sessions": """every run auto-saves to ~/.mule/sessions/.
  mule --list-sessions            list them
  mule --continue                 resume the newest
  mule --fork NAME                branch off one
  mule --search-sessions QUERY    search them
  mule sessions rename OLD NEW    rename one
  mule sessions rm NAME           delete one
  mule sessions stats             message counts""",
    "examples": EXAMPLES,
}


def cmd_help(rest):
    # mule help [tools|config|sessions|examples]
    if not rest:
        print("usage: mule help tools|config|sessions|examples")
        return 2
    topic = rest[0].lower()
    if topic not in HELP_TOPICS:
        print("unknown help topic: %s" % rest[0], file=sys.stderr)
        return 2
    print(HELP_TOPICS[topic])
    return 0


def cmd_sessions(rest):
    # mule sessions list | rename OLD NEW | rm NAME | stats
    if not rest or rest[0] not in ("list", "rename", "rm", "stats"):
        print("usage: mule sessions list | rename OLD NEW | rm NAME | stats",
              file=sys.stderr)
        return 2
    action = rest[0]
    try:
        if action == "list":
            for name in list_sessions():
                print(name)
        elif action == "rename":
            if len(rest) < 3:
                print("usage: mule sessions rename OLD NEW",
                      file=sys.stderr)
                return 2
            print("renamed: %s" % rename_session(rest[1], rest[2]))
        elif action == "rm":
            if len(rest) < 2:
                print("usage: mule sessions rm NAME", file=sys.stderr)
                return 2
            print("removed: %s" % delete_session(rest[1]))
        elif action == "stats":
            rows = session_stats()
            if not rows:
                print("(no sessions)")
            for r in rows:
                print("%-24s %4d messages %8d bytes"
                      % (r["name"], r["messages"], r["bytes"]))
    except ValueError as e:
        print(red("error: %s" % e), file=sys.stderr)
        return EXIT_ERROR
    return 0


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
        return EXIT_ERROR
    return 0


def ask_cmd(command):
    # the human-in-the-loop gate for --ask
    print(yellow("run this? %s" % command))
    ans = input("[y/N] ").strip().lower()
    return ans in ("y", "yes")


def ask_critical(command):
    # the point of no return: only the literal word "yes" runs it
    print(red("dangerous command: %s" % command))
    print(red("type 'yes' to run it, anything else aborts"))
    return input("> ").strip() == "yes"


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


def apply_tool_gates(args, tools):
    # --deny-tools, --allow-tools, --readonly, --no-network:
    # shrink what the model may call
    if args.allow_network:
        args.no_network = False
    if args.no_network:
        for name in ("fetch_url", "web_search", "http_post"):
            tools.disable_tool(name)
    if args.readonly:
        for name in ("write_file", "edit_file", "apply_patch", "run_shell"):
            tools.disable_tool(name)
    if args.deny_tools:
        for name in args.deny_tools.split(","):
            name = name.strip()
            if name:
                tools.disable_tool(name)
    if args.allow_tools:
        allowed = {n.strip() for n in args.allow_tools.split(",") if n.strip()}
        for name in list(tools.tools):
            if name not in allowed:
                tools.disable_tool(name)


def load_template(root, name):
    # .mule/templates/NAME.md under the project root
    filename = name if name.endswith(".md") else name + ".md"
    path = os.path.join(os.path.abspath(root), ".mule", "templates",
                        filename)
    if not os.path.isfile(path):
        raise ValueError("no such template: %s (looked in %s)"
                         % (name, os.path.dirname(path)))
    with open(path) as f:
        return f.read()


def make_chat_fn(args, client):
    # wraps the client with --fallback-model: if the primary model
    # raises, switch once and retry. model_box tracks which model
    # actually answered, so cost math stays honest.
    model_box = {"model": args.model, "fell_back": False}

    def _once(fn, messages, tools, **kw):
        try:
            return fn(messages, tools, **kw)
        except RuntimeError:
            if args.fallback_model and not model_box["fell_back"]:
                model_box["fell_back"] = True
                model_box["model"] = args.fallback_model
                client.model = args.fallback_model
                say(args, "primary model failed, falling back to %s"
                          % args.fallback_model)
                return fn(messages, tools, **kw)
            raise

    def chat_fn(messages, tools):
        stop = args.stop.split(",") if args.stop else None
        # stream tokens live unless --no-stream was passed.
        # --print stays silent, it only wants the final answer.
        if args.no_stream or getattr(args, "print_mode", False):
            return _once(client.chat, messages, tools, stop=stop)
        printed = []

        def on_token(t):
            if args.quiet or args.json:
                return
            print(t, end="", flush=True)
            printed.append(t)

        reply = _once(client.chat_stream, messages, tools,
                      on_token=on_token, stop=stop)
        if printed and not args.quiet and not args.json:
            print()
        return reply

    chat_fn.model_box = model_box

    def set_model(name):
        # switch models mid-session, cost tracking follows along
        client.model = name
        model_box["model"] = name

    chat_fn.set_model = set_model
    return chat_fn


def say(args, msg):
    # info lines go to stderr in --json mode, keeping stdout pure json
    if args.json:
        print(msg, file=sys.stderr)
    else:
        print(msg)


def build_result(args, messages, totals, model_box=None):
    # the machine-readable result for --json
    model = model_box["model"] if model_box else args.model
    return {
        "answer": last_answer(messages),
        "steps": sum(1 for m in messages
                     if m.get("role") == "assistant" and m.get("tool_calls")),
        "model": model,
        "tokens_in": totals["in"],
        "tokens_out": totals["out"],
        "cost": cost_for(model, totals["in"], totals["out"]),
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


def make_track(args, totals, model_box=None):
    def track(step, usage):
        # returns True when the cost budget is blown, stopping the loop
        model = model_box["model"] if model_box else args.model
        if usage:
            pin = usage.get("prompt_tokens", 0)
            pout = usage.get("completion_tokens", 0)
            totals["in"] += pin
            totals["out"] += pout
            if not args.quiet and not args.json and not args.print_mode:
                print("  [step %d: %s in / %s out, %s]" % (
                    step, "{:,}".format(pin), "{:,}".format(pout),
                    fmt_cost(cost_for(model, pin, pout))))
        if args.max_cost is not None:
            spent = cost_for(model, totals["in"], totals["out"])
            if spent is not None and spent > args.max_cost:
                return True
        return False
    return track


def make_timing(args):
    # per-step durations for --verbose
    def on_timing(step, seconds):
        if args.verbose and not args.quiet and not args.json:
            print("  [step %d took %.1fs]" % (step, seconds))
    return on_timing


def validate_schema(answer, schema):
    # tiny validator: answer must be json with the required keys.
    # returns None when valid, else a human-readable error.
    try:
        data = json.loads(answer)
    except ValueError:
        return "the answer is not valid json"
    required = schema.get("required") or []
    if not isinstance(data, dict):
        return "the answer must be a json object"
    missing = [k for k in required if k not in data]
    if missing:
        return "missing required keys: %s" % ", ".join(missing)
    return None


def enforce_schema(args, chat_fn, messages, track, step):
    # --schema: check the final answer, give the model one
    # chance to fix it when it does not validate
    try:
        with open(args.schema) as f:
            schema = json.load(f)
    except (OSError, ValueError) as e:
        print(red("error: bad schema file: %s" % e), file=sys.stderr)
        return messages
    if not isinstance(schema, dict):
        print(red("error: schema file must be a json object"),
              file=sys.stderr)
        return messages
    err = validate_schema(last_answer(messages), schema)
    if err is None:
        return messages
    if not args.quiet:
        say(args, "schema check failed (%s), asking for a fix" % err)
    messages.append({
        "role": "user",
        "content": "your last answer failed validation: %s. the schema "
                   "is %s. reply with only the corrected json."
                   % (err, json.dumps(schema)),
    })
    reply = chat_fn(messages, [])
    track(step, reply.get("usage"))
    messages.append(_clean_reply(reply))
    err = validate_schema(last_answer(messages), schema)
    if err is not None and not args.quiet:
        say(args, "warning: answer still does not validate: %s" % err)
    return messages


def reflect_answer(args, chat_fn, messages, track, step):
    # --reflect: one extra model call to critique the final
    # answer, then the improved version becomes the answer
    if not args.quiet:
        say(args, "reflecting on the answer...")
    messages.append({
        "role": "user",
        "content": "review your final answer above. reply with a short "
                   "critique, then an improved version of the answer.",
    })
    reply = chat_fn(messages, [])
    track(step, reply.get("usage"))
    messages.append(_clean_reply(reply))
    return messages


def run_interactive(args, tools, system, messages, task,
                    chat_fn, show, track, totals, todos):
    # prompt loop: each line is a task, history carries over
    from repl import compress_history
    box = {"messages": messages}
    commands = load_commands(
        os.path.join(os.path.abspath(args.root), ".mule", "commands"))

    def save_fn(name):
        return save_session(name, box["messages"])

    def set_model(name):
        chat_fn.set_model(name)
        ctx["model"] = name

    ctx = {"write": print, "tools": tools, "totals": totals,
           "model": args.model, "save_fn": save_fn,
           "commands": commands, "set_model": set_model,
           "last_task": None, "version": __version__}

    def on_slash(line):
        action = handle_slash(line, ctx)
        if action == "clear":
            box["messages"] = [{"role": "system", "content": system}]
            print("history cleared")
        elif action == "compress":
            box["messages"] = compress_history(chat_fn, box["messages"],
                                               track)
            print("history compressed to a recap")
        elif isinstance(action, tuple) and action[0] == "run":
            # a custom command becomes the next task
            on_task(action[1])
            return None
        return action

    def on_task(line):
        ctx["last_task"] = line
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
    # persistent input history across sessions
    histfile = os.path.expanduser("~/.mule/history")
    os.makedirs(os.path.dirname(histfile), exist_ok=True)
    try:
        import readline
    except ImportError:
        readline = None
    if readline is not None:
        try:
            readline.read_history_file(histfile)
        except OSError:
            pass
        import atexit
        atexit.register(readline.write_history_file, histfile)
    repl_loop(input, print, on_task, on_slash)
    if len(box["messages"]) > 1:
        path = save_session(auto_name(), box["messages"])
        print("auto-saved session: %s" % path)
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


class _Tee:
    # mirrors everything printed on stdout into a log file,
    # so --log-file captures the whole run transcript
    def __init__(self, path):
        self.file = open(path, "a")
        self.stdout = sys.stdout

    def write(self, s):
        self.stdout.write(s)
        self.file.write(s)

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        try:
            self.file.close()
        except OSError:
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        return run_subcommand(argv[0], argv[1:])
    args = parse_args(argv)
    init_color(args.no_color)
    if not args.log_file:
        return _run(args)
    try:
        tee = _Tee(args.log_file)
    except OSError as e:
        print(red("error: cannot open log file: %s" % e),
              file=sys.stderr)
        return EXIT_ERROR
    old_stdout = sys.stdout
    sys.stdout = tee
    try:
        return _run(args)
    finally:
        sys.stdout = old_stdout
        tee.close()


def _run(args):
    if args.list_sessions:
        for name in list_sessions():
            print(name)
        return 0

    if args.search_sessions:
        hits = search_sessions(args.search_sessions)
        if not hits:
            print("no sessions matching %r" % args.search_sessions)
        for name, snippet in hits:
            print("%s: %s" % (name, snippet))
        return 0

    tools = ToolSet(args.root,
                    confirm=ask_cmd if (args.ask and not args.print_mode)
                    else None,
                    confirm_critical=ask_critical
                    if (args.ask and not args.print_mode) else None,
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
    apply_tool_gates(args, tools)
    if args.undo:
        print(tools.undo_last())
        return 0
    if args.restore:
        from checkpoints import restore_checkpoint
        try:
            path = restore_checkpoint(args.root, args.restore)
        except ValueError as e:
            print(red("error: %s" % e), file=sys.stderr)
            return EXIT_ERROR
        print("restored checkpoint: %s" % path)
        return 0
    if args.checkpoint:
        from checkpoints import save_checkpoint
        path = save_checkpoint(args.root, args.checkpoint)
        if not args.quiet:
            say(args, "checkpoint saved: %s" % path)

    task = resolve_task(args, sys.stdin)
    if args.template:
        # the template wraps the task: {{task}} becomes the task text
        try:
            task = load_template(args.root, args.template).replace(
                "{{task}}", task or "")
        except ValueError as e:
            print(red("error: %s" % e), file=sys.stderr)
            return EXIT_ERROR
    if not task and not args.resume and not args.cont and not args.interactive \
            and not args.import_:
        print(red("give it a task, as an argument or on stdin"),
              file=sys.stderr)
        return 2
    if not args.api_key:
        print(red("set OPENAI_API_KEY or pass --api-key"), file=sys.stderr)
        return 2

    client = ChatClient(args.base_url, args.api_key, args.model,
                        timeout=args.timeout, retries=args.retries,
                        temperature=args.temperature,
                        max_tokens=args.max_tokens, seed=args.seed,
                        trace_file=args.trace)
    tools.make_chat = lambda: make_chat_fn(args, client)
    system = resolve_system(args)

    messages = None
    if args.resume:
        try:
            messages = load_session(args.resume)
        except ValueError as e:
            print(red("error: %s" % e), file=sys.stderr)
            return EXIT_ERROR
    if args.fork and messages is None:
        # branch off a saved session: same start, but saving later
        # needs an explicit name, so the original stays untouched
        try:
            messages = load_session(args.fork)
        except ValueError as e:
            print(red("error: %s" % e), file=sys.stderr)
            return EXIT_ERROR
        if not args.quiet and not args.print_mode:
            say(args, "forked from session: %s" % args.fork)
    if args.import_ and messages is None:
        # start from a markdown or jsonl history file
        from sessions import import_history
        try:
            messages = import_history(args.import_)
        except (ValueError, OSError) as e:
            print(red("error: %s" % e), file=sys.stderr)
            return EXIT_ERROR
        if not args.quiet and not args.print_mode:
            say(args, "imported %d messages from %s"
                      % (len(messages), args.import_))
    if args.cont and messages is None:
        latest = latest_session()
        if latest is None:
            print(red("error: no saved sessions to continue"),
                  file=sys.stderr)
            return EXIT_ERROR
        try:
            messages = load_session(latest)
        except ValueError as e:
            print(red("error: %s" % e), file=sys.stderr)
            return EXIT_ERROR
        if not args.quiet and not args.print_mode:
            say(args, "continuing session: %s" % latest)
    if args.interactive and messages is None:
        messages = [{"role": "system", "content": system}]

    chat_fn = make_chat_fn(args, client)
    model_box = chat_fn.model_box
    show = make_show(args)
    totals = {"in": 0, "out": 0}
    track = make_track(args, totals, model_box)
    todos = make_todos(args)
    timing = make_timing(args)

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
                       dry_run=args.dry_run,
                       max_tools=args.max_tools,
                       time_limit=args.time_limit,
                       on_timing=timing,
                       parallel_tools=args.parallel_tools,
                       tool_timeout=args.tool_timeout)
    except RuntimeError as e:
        print(red("error: %s" % e), file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print(red("\ncancelled"), file=sys.stderr)
        return EXIT_CANCELLED

    step_no = sum(1 for m in messages
                  if m.get("role") == "assistant") + 1
    final = last_answer(messages)
    if args.reflect and final and not final.startswith("stopped:"):
        messages = reflect_answer(args, chat_fn, messages, track, step_no)
        step_no += 1
        final = last_answer(messages)
    exit_code = EXIT_BUDGET if final == "stopped: hit cost budget" \
        else EXIT_OK
    if args.schema and final and not final.startswith("stopped:"):
        messages = enforce_schema(args, chat_fn, messages, track, step_no)

    if args.save:
        name = auto_name() if args.save == "auto" else args.save
        path = save_session(name, messages)
        if not args.quiet:
            say(args, "saved session: %s" % path)
    else:
        # every run is kept, named by timestamp, so --continue
        # and --search-sessions always have something to find.
        # stays silent in --print mode, which wants stdout clean.
        path = save_session(auto_name(), messages)
        if not args.quiet and not args.print_mode:
            say(args, "auto-saved session: %s" % path)

    used_model = model_box["model"]
    cost_line = "tokens: %s in / %s out, cost %s" % (
        "{:,}".format(totals["in"]), "{:,}".format(totals["out"]),
        fmt_cost(cost_for(used_model, totals["in"], totals["out"])))
    if args.export:
        from sessions import export_session
        export_session(args.export, messages, cost_line=cost_line)
        if not args.quiet:
            say(args, "exported: %s" % args.export)

    if args.json:
        print(json.dumps(build_result(args, messages, totals, model_box),
                         indent=2))
        return exit_code

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
        return exit_code

    if not args.quiet:
        print("---")
        print(cost_line)
    print(last_answer(messages))
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(red("\ncancelled"), file=sys.stderr)
        sys.exit(EXIT_CANCELLED)
