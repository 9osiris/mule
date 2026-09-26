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

- `task` — what to do (or pipe it on stdin)
- `--model` — model name (default: gpt-4o-mini, or MULE_MODEL)
- `--base-url` — api base (default: https://api.openai.com/v1, or MULE_BASE_URL)
- `--api-key` — defaults to OPENAI_API_KEY
- `--root` — project dir the agent is sandboxed to (default: .)
- `--max-steps` — tool rounds before it gives up (default: 25)
- `--system-prompt` — path to a custom prompt file (default: prompt.md)
- `--quiet` — only print the final answer

## how it works

`agent.py` runs the loop: send messages, take the model's tool calls,
run them, feed results back, repeat. `tools.py` has four tools —
read_file, write_file, list_dir, run_shell — all sandboxed to `--root`
so the agent can't wander out of the project dir. `client.py` is the
http client for /v1/chat/completions. `prompt.md` is the system prompt.

## safety notes

this thing runs shell commands for you. only point `--root` at
directories you don't mind being changed, and read the task output.
run_shell has a timeout and refuses interactive commands by design
(they'd just hang until the timeout), but a model can still do
dumb stuff like `rm -rf` inside the root. you were warned.

## tests

```bash
python test_agent.py
```

spins a fake openai-compatible server locally and runs the full loop
through it, no api key needed.
