# mule

a minimal coding agent that runs against any openai-compatible chat
completions api. give it a task, it reads/writes files and runs shell
commands until the job is done or it hits the step limit.

no dependencies, stdlib only.

## quick start

```bash
export OPENAI_API_KEY=sk-...
python main.py "add a hello world function to app.py" --root ./myproject
```

point it at something else with `--base-url`:

```bash
python main.py "summarize the repo layout" \
  --base-url http://localhost:11434/v1 \
  --api-key ollama \
  --model qwen2.5-coder \
  --root ./myproject
```

## flags

- `task` - what to do (or pipe it on stdin)
- `--model` - model name (default: gpt-4o-mini, or MULE_MODEL)
- `--base-url` - api base (default: https://api.openai.com/v1, or MULE_BASE_URL)
- `--api-key` - defaults to OPENAI_API_KEY
- `--root` - project dir the agent is sandboxed to (default: .)
- `--max-steps` - tool rounds before it gives up (default: 25)
- `--system-prompt` - path to a custom prompt file (default: prompt.md)
- `--quiet` - only print the final answer
- `--no-stream` - wait for the full response instead of streaming tokens
- `--save [NAME]` - save the conversation to `~/.mule/sessions/`
  (auto-names with a timestamp if no name given)
- `--resume NAME` - resume a saved session, then continue with the task
- `--list-sessions` - list saved sessions and exit
- `--ask` - ask for confirmation before each shell command
- `--undo` - restore the most recently changed file and exit
- `--interactive` - prompt loop for follow-up tasks
- `--timeout SEC` - api request timeout (default: 120)
- `--retries N` - retries on 429/5xx with exponential backoff (default: 3)
- `--max-cost DOLLARS` - stop the agent when session cost exceeds this
- `--plan` - the model writes a plan first; you approve (y), reject (n),
  or ask for one revision (r) before any tool runs
- `--context-budget CHARS` - squash old history into a summary past this
  many chars of conversation (default: 100000)
- `--export PATH` - write the session transcript to a markdown file
- `--checkpoint NAME` - tar the project root to `~/.mule/checkpoints/`
  before the run (skips .git and caches)
- `--restore NAME` - restore a checkpoint over the project root and exit
- `--json` - print the result as json (answer, steps, tokens, cost)
  for scripting; info lines go to stderr so stdout stays pure json
- `--dry-run` - run the loop but never execute tools: each planned
  call prints as `dry run, not executed: name(args)` instead
- `--system TEXT` - replace the system prompt with this text
- `--append-system TEXT` - append extra instructions to the system prompt
- `--no-color` - disable ansi colors (also honors the NO_COLOR env var)

## subcommands

- `mule init [--force]` - scaffold a project: writes a starter
  `mule.json`, a `prompt.md` template, and an example command in
  `.mule/commands/`. refuses to overwrite unless `--force`.
- `mule config [--global] get KEY | set KEY VALUE | unset KEY | list`
  - read and write config values from the cli. writes the local
  `mule.json` by default, `--global` targets `~/.config/mule/mule.json`.
  values that look like json become json (`set max_steps 40` stores
  the number 40).
- `mule doctor [--root DIR]` - sanity checklist: configs parse, an
  api key is set (never printed), the base url answers, the project
  root exists. exits 1 when anything fails.
- `mule completion bash|zsh|fish` - print a completion script for
  your shell. the flag list is generated from the real parser so it
  never drifts.
- `mule models` - table of priced models ($/1M tokens in/out) from
  `cost.py`, no api call needed.

the task can also come from stdin: `echo "fix the bug" | mule`
or `mule -` reads it explicitly.

## config file

`mule.json` in the current directory sets defaults, and
`~/.config/mule/mule.json` sets global ones. the local file wins
over the global one. recognized keys: `model`, `base_url`,
`api_key`, `max_steps`, `timeout`, `retries`, `context_budget`, `root`.

```json
{
  "model": "qwen2.5-coder",
  "base_url": "http://localhost:11434/v1",
  "api_key": "ollama",
  "max_steps": 40
}
```

precedence: flags beat env vars (`MULE_MODEL`, `MULE_BASE_URL`,
`MULE_MAX_STEPS`, `MULE_TIMEOUT`, `MULE_RETRIES`, `MULE_CONTEXT_BUDGET`, `OPENAI_API_KEY`) beat the local
config beat the global config beat the built-in defaults. you can
put `api_key` in the config, but an env var is safer than a key
sitting in a file. manage values without opening an editor:
`mule config set model gpt-4o-mini`, `mule config list`.

responses stream by default: tokens print as they arrive. pass
`--no-stream` to go back to waiting for the whole reply.

sessions are jsonl, one message per line. resume like this:

```bash
python main.py "add tests for the parser" --save parser-work
# later
python main.py --resume parser-work "now fix the failing test"
```

## interactive mode

`--interactive` starts a prompt loop. type a task, get an answer,
type a follow-up; the conversation history carries over between
turns. slash commands:

- `/help` - show the commands
- `/quit` - leave the loop
- `/clear` - reset the conversation history
- `/save NAME` - save the session to `~/.mule/sessions/`
- `/cost` - show tokens and spend so far
- `/undo` - restore the most recently changed file

drop markdown files in `<root>/.mule/commands/` and they become
custom slash commands: `review.md` becomes `/review`, and its
content runs as the next task. `/help` lists them.

a task on the command line runs first, then the loop takes over:

```bash
python main.py --interactive "refactor the parser"
```

file changes are backed up before every write or edit, so `/undo`
(or `--undo` for one-shot runs) restores the most recent one. in
`--ask` mode, file writes show a unified diff in the confirmation
prompt before anything is applied.

## cost tracking

each step prints its token counts and rough cost, with a session total
at the end. `--max-cost 1.50` stops the loop with a clean message once
the session spend passes the budget (models without a pricing row
never trip it). pricing lives in `cost.py` (dollars per 1m tokens, update
as prices move); models not in the table show "unknown pricing".

long runs don't blow up the context: past `--context-budget` chars of
history, the oldest messages get squashed into a summary by one model
call (system prompt and the last 10 messages stay intact). the summary
call counts toward the cost totals like any other step.

## plan mode, todos, and checkpoints

`--plan` makes the model write its plan before touching anything. you
approve it, reject it, or ask for one revision; only an approved plan
reaches the tool loop.

for multi-step work the model can keep a todo list with `todo_write`
(`pending`/`in_progress`/`done`) and read it back with `todo_read`;
the loop prints a compact `[2/5] current thing` line whenever it
changes.

`--checkpoint NAME` tars the project root into
`~/.mule/checkpoints/` before a run (skips `.git`, `__pycache__`,
and `.mule`), and `--restore NAME` unpacks one back over the
project. cheap insurance before letting the agent loose.

`--export PATH` writes the finished session to a readable markdown
file: turns as `## user` / `## assistant`, tool calls as code
blocks, cost at the bottom.

## how it works

`agent.py` runs the loop: send messages, take the model's tool calls,
run them, feed results back, repeat. it also handles plan approval,
todo progress lines, context compaction, running several tool
calls from one turn in order, and `--dry-run` (planned calls print
instead of executing). `tools.py` has ten tools -
read_file, write_file, edit_file, list_dir, run_shell, fetch_url,
web_search, todo_write, todo_read, read_image - all sandboxed to
`--root` so the agent can't wander out of the project dir (fetch_url
only does http/https, read_image only loads png/jpg/gif/webp).
`client.py` is the http client for /v1/chat/completions, with
streaming support, token usage capture, and retries with
exponential backoff on 429s and 5xxs. `cost.py` holds rough
per-model pricing. `sessions.py` saves and resumes conversations
as jsonl files under `~/.mule/sessions/` and exports them to
markdown. `checkpoints.py` tars and restores project snapshots.
`prompt.md` is the system prompt. `repl.py` runs the
`--interactive` prompt loop, its slash commands, and custom
commands from `.mule/commands/`. `scaffold.py` powers `mule init`.
`doctor.py` runs the `mule doctor` checklist. `complete.py`
generates the shell completion scripts from the real argument
parser. `ui.py` is the tiny ansi color layer, off with
`--no-color` or NO_COLOR.

## tools

- `read_file` - read a text file
- `write_file` - write a whole file (creates parent dirs, overwrites)
- `edit_file` - replace one exact string in a file, must match once
- `list_dir` - list a directory
- `run_shell` - run a shell command in the project root
- `fetch_url` - fetch a web page, html stripped to rough text
- `web_search` - search the web via duckduckgo, returns titles,
  urls, and snippets
- `todo_write` - replace the todo list (json list of {text, status})
- `todo_read` - show the current todo list
- `read_image` - load a png/jpg/gif/webp as a base64 data uri

## safety notes

this thing runs shell commands for you. only point `--root` at
directories you don't mind being changed, and read the task output.
run_shell has a timeout and refuses interactive commands by design
(they'd just hang until the timeout), but a model can still do
dumb stuff like `rm -rf` inside the root. pass `--ask` to approve
every shell command and file write yourself before it runs (file
writes show a diff first). you were warned.

## tests

```bash
python test_agent.py
```

spins a fake openai-compatible server locally and runs the full loop
through it, no api key needed.
