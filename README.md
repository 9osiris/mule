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

agentrouter works too (mule sends the client headers its waf wants):

```bash
python main.py "check this for errors" \
  --base-url https://agentrouter.org/v1 \
  --api-key $AGENTROUTER_API_KEY \
  --model gpt-4o-mini \
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
- `--print` - print only the final answer, no confirmations, for scripting
- `--profile NAME` - use a saved profile from `~/.mule/profiles/`:
  cli flags beat the profile, the profile beats `mule.json`
- `--output FILE` - write the final answer to FILE too (works with
  `--print`)
- `--temperature F` - sampling temperature, lower is more focused
- `--max-tokens N` - cap on completion tokens per request
- `--seed N` - seed for reproducible outputs
- `--continue` - pick up the most recent saved session, then continue
  with the task
- `--stop SEQS` - comma-separated stop sequences sent to the api
- `--max-tools N` - stop the loop after N tool calls total
- `--time-limit SEC` - stop the loop after SEC seconds
- `--parallel-tools` - run independent tool calls in the same step
  together (results stay in order)
- `--tool-timeout SEC` - cut off any single tool call after SEC
  seconds instead of waiting on it
- `--reflect` - after answering, the model critiques and improves
  its own answer once
- `--fallback-model NAME` - if the primary model errors, retry the
  failed call once on this model
- `--schema KEYS` - comma-separated keys the final answer must be a
  json object containing (the model gets one fix-up try)
- `--fork NAME` - start from a saved session's history without
  touching the original
- `--search-sessions QUERY` - search saved sessions and exit
- `--verbose` - show per-step timing and token lines
- `--allow-tools LIST` - comma-separated tools the model may call,
  everything else is hidden
- `--deny-tools LIST` - comma-separated tools the model may not call
- `--readonly` - disable every tool that writes: no file writes,
  no shell, no network posts
- `--no-network` - disable `fetch_url`, `web_search`, `http_post`
- `--allow-network URL` - re-enable one host after `--no-network`
- `--template NAME` - run the task through
  `.mule/templates/NAME.md`; `{{task}}` becomes your task text
- `--import FILE` - start from a `.md` or `.jsonl` history file
- `--log-file FILE` - append all output to FILE as well as the terminal
- `--trace FILE` - write raw api request/response pairs to FILE as
  jsonl (the api key is never recorded)
- `--version` - print the version and exit

exit codes: `0` ok, `2` cost budget hit or usage error, `3` runtime
error, `130` you pressed ctrl-c.

## subcommands

- `mule init [--force]` - scaffold a project: writes a starter
  `mule.json`, a `prompt.md` template, an example command in
  `.mule/commands/`, and an example task template in
  `.mule/templates/`. refuses to overwrite unless `--force`.
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
- `mule sessions rename OLD NEW | rm NAME | stats` - rename or
  delete a saved session, or show per-session message counts.
- `mule serve [--port 8321] [--no-browser]` - local web chat ui,
  gpt4all-style: sidebar with sessions, streaming markdown, tool
  calls shown as a compact transcript. one self-contained page,
  no build step, no external requests. bound to 127.0.0.1 only,
  shares every model/root flag with a normal run.
- `mule help tools|config|sessions|serve|examples` - topic help.
- `mule examples` - copy-pasteable example invocations.
- `mule demo` - run the whole loop against a fake local model, no
  api key needed. good for kicking the tires.

the task can also come from stdin: `echo "fix the bug" | mule`
or `mule -` reads it explicitly.

## config file

`mule.json` in the current directory sets defaults, and
`~/.config/mule/mule.json` sets global ones. the local file wins
over the global one. recognized keys: `model`, `base_url`,
`api_key`, `max_steps`, `timeout`, `retries`, `context_budget`, `root`,
`ask`, `reflect`, `verbose`, `parallel_tools`, `readonly`, `no_network`,
`allow_tools`, `deny_tools`, `fallback_model`, `max_tools`, `time_limit`,
`temperature`, `log_file`, `trace`, `tool_timeout`. unknown keys and
wrong types get a warning on stderr naming the file, the key, and
what it should be.

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

every run auto-saves (timestamped) even without `--save`, so
`--continue`, `--search-sessions`, and `mule sessions stats` always
have something to find. manage them:

```bash
mule --search-sessions "parser bug"
mule sessions rename session-20260926-120000 parser-work
mule sessions rm parser-work
mule sessions stats
mule --fork parser-work "try a different approach"
```

## templates

`--template NAME` reads `.mule/templates/NAME.md` and substitutes
`{{task}}` with your task text, so the template wraps the task.
`mule init` ships a `review` example:

```bash
mule --template review "main.py"
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
- `/tools` - list available tools
- `/tools off NAME` / `/tools on NAME` - disable or re-enable a
  tool for this session
- `/model [NAME]` - show the current model or switch it mid-run
- `/retry` - run the last task again
- `/compress` - squash history into a short recap to free context
- `/undo` - restore the most recently changed file
- `/bug [TEXT]` - print a prefilled github issue url for the repo
- `!CMD` - run a shell command directly, the agent never sees it
- `@PATH` - attach a file's contents to your task

just chatting works too: greetings, thanks, and simple questions
get a direct reply, no tool calls.

wrap input in triple backticks for multiline tasks. prompt history
persists across runs in `~/.mule/history` (up/down arrows work).

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

## web ui

`mule serve` starts a local chat app in your browser, in the style
of gpt4all's desktop ui: a sidebar with your saved sessions, a
composer, streamed markdown replies, and tool calls rendered as a
compact transcript you can expand. everything is one
self-contained page (`webui.html`): no build step, no cdn, no
external requests, and the server only listens on 127.0.0.1.

```bash
mule serve                                # opens http://127.0.0.1:8321/
mule serve --port 9000 --root ~/proj --model deepseek-v4-flash
mule serve --no-browser                   # just print the url
```

chats in the web ui save to the same `~/.mule/sessions/` files as
the cli, so you can start in the browser and resume with
`mule --resume NAME`. one chat runs at a time; a second request
while one is streaming gets a 409 until it finishes.

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

## skills and plugins

drop a folder with a `SKILL.md` in `<root>/.mule/skills/` and its
contents get appended to the system prompt for every run:

```
.mule/skills/
  tdd/
    SKILL.md    # "write a failing test before you change code"
```

for real code, drop python files in `~/.mule/plugins/`. each file
exposes a `get_tools()` function returning a list of tool dicts,
and they show up alongside the builtins (a bad plugin prints a
warning, it never breaks startup):

```python
def get_tools():
    return [{
        "name": "shout",
        "description": "yell some text",
        "parameters": {"text": "what to yell"},
        "handler": lambda text="": text.upper(),
    }]
```

`/tools` in interactive mode lists everything available, plugins
included.

## how it works

`agent.py` runs the loop: send messages, take the model's tool calls,
run them, feed results back, repeat. it also handles plan approval,
todo progress lines, context compaction, running several tool
calls from one turn in order, and `--dry-run` (planned calls print
instead of executing). `tools.py` has fifteen tools -
read_file, write_file, edit_file, list_dir, run_shell, fetch_url,
web_search, todo_write, todo_read, read_image, delegate, jobs,
job_output, job_kill, ask_user - all sandboxed to `--root` so the
agent can't wander out of the project dir (fetch_url only does
http/https, read_image only loads png/jpg/gif/webp). `client.py`
is the http client for /v1/chat/completions, with streaming
support, token usage capture, optional temperature/max_tokens/seed,
and retries with exponential backoff on 429s and 5xxs. `cost.py`
holds rough per-model pricing. `sessions.py` saves and resumes
conversations as jsonl files under `~/.mule/sessions/` and exports
them to markdown. `checkpoints.py` tars and restores project
snapshots. `prompt.md` is the system prompt: a 100-section engineering
playbook (verify before assuming, smallest correct change, test
everything, disciplined correction). it also tells the model to narrate
as it goes: one short line before each batch of tool calls, so
the human can follow along. `repl.py` runs the
`--interactive` prompt loop, its slash commands, and custom
commands from `.mule/commands/`. `scaffold.py` powers `mule init`.
`doctor.py` runs the `mule doctor` checklist. `complete.py`
generates the shell completion scripts from the real argument
parser. `skills.py` loads `.mule/skills/*/SKILL.md` into the
system prompt. `plugins.py` loads python tool plugins from
`~/.mule/plugins/`. `ui.py` is the tiny ansi color layer, off with
`--no-color` or NO_COLOR.

## tools

- `read_file` - read a text file
- `write_file` - write a whole file (creates parent dirs, overwrites)
- `edit_file` - replace one exact string in a file, must match once
- `apply_patch` - atomic batch of {path, old, new} edits across
  files: every old string must match exactly once or nothing applies
- `list_dir` - list a directory
- `tree` - directory structure as a tree
- `find` - locate files by name pattern
- `grep` - search file contents for a regex
- `read_many` - read up to 10 files in one call
- `file_info` - size, mtime, and type for a path
- `run_shell` - run a shell command in the project root; pass
  `background=true` to get a job id back instead of output
- `git_status`, `git_diff`, `git_log` - read-only git inspection
- `fetch_url` - fetch a web page, html stripped to rough text
- `web_search` - search the web via duckduckgo, returns titles,
  urls, and snippets
- `http_post` - post to a url (only works in `--ask` mode, after
  confirmation)
- `todo_write` - replace the todo list (json list of {text, status})
- `todo_read` - show the current todo list
- `read_image` - load a png/jpg/gif/webp as a base64 data uri
- `delegate` - hand a subtask to a subagent with its own history,
  returns a summary (subagents can't delegate further)
- `jobs` - list background shell jobs with status and output preview
- `job_output` - full output of a background job
- `job_kill` - stop a running background job
- `ask_user` - ask the human a question with 2-4 options (only
  works in interactive or `--ask` mode)

## safety notes

this thing runs shell commands for you. only point `--root` at
directories you don't mind being changed, and read the task output.
run_shell has a timeout and refuses interactive commands by design
(they'd just hang until the timeout), but a model can still do
dumb stuff like `rm -rf` inside the root. pass `--ask` to approve
every shell command and file write yourself before it runs (file
writes show a diff first). you were warned.

on top of that:

- a denylist always refuses the truly awful stuff: writing to raw
  disks (`> /dev/sda`), `chmod -R 777 /`. no prompt, just refused.
- `rm -rf /`, `mkfs`, `dd` to a device, and fork bombs need the
  literal word `yes` typed in `--ask` mode. without a human at the
  keyboard they are refused outright.
- when a file read looks like it contains secrets (api keys,
  tokens, private keys, `password = ...`), you get a loud warning
  before it goes to the model.
- `--readonly` turns off every writing tool; `--no-network` turns
  off the network ones; `--allow-tools`/`--deny-tools` pick exactly
  which tools the model may call.

## exit codes

`0` the run finished, `2` the cost budget was hit (or you misused a
flag), `3` something failed at runtime, `130` you pressed ctrl-c.
script against them: `mule --print "task" || echo "failed: $?"`.

## the look

mule has a face: a geometric mule head in bold amber opens every
run, with the version, model, and project root on one line under
it. tool calls render as compact transcript lines (an amber glyph,
the name, the args) with results nested underneath, a delayed
spinner ticks while the model thinks, and a footer closes the run
with the model, token counts, and cost. replies render as
markdown, printed once: never streamed raw and then repeated.
full spec in `BRANDING.md`.

on terminals that render unicode you get `⏺ ⎿ ❯ ─ ◷`; on legacy
windows consoles, where those show up as boxes, mule falls back
to plain ascii (`* | > -`) automatically. colors work in both.

install `rich` for the full treatment: syntax-highlighted code
blocks and live markdown streaming as tokens arrive.

```bash
pip install rich        # or: pip install mule[rich]
```

without it, everything still works on plain stdlib: a spinner
while the model thinks, then the reply rendered once, same
transcript. `--no-color` (or the `NO_COLOR` env var) turns all of
it off; `--print`, `--json`, and `--quiet` never show it in the
first place.

## tests

```bash
python test_agent.py
python test_serve.py
```

spins a fake openai-compatible server locally and runs the full loop
through it, no api key needed. `test_serve.py` boots the real web
server on a temp port with a fake client and exercises every
endpoint, including the sse chat stream.
