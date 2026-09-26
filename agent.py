import json
import os


SYSTEM_PROMPT = """You are a coding agent working inside a project directory.
You have tools to read files, write files, edit files, list directories,
run shell commands, and fetch web pages.
All paths are relative to the project root. Use them for everything.

Rules:
- Read before you change. Look at the files involved first.
- Prefer edit_file for small changes, write_file only for new files.
- Do one thing at a time. After each tool result, decide the next step.
- Keep shell commands simple and non-interactive. Never run anything that waits for input.
- When the task is fully done, reply with a short summary of what you did and make no more tool calls.
- If you are stuck after several tries, say so and stop instead of looping forever.
"""


def load_system_prompt(path=None):
    if path and os.path.isfile(path):
        with open(path) as f:
            return f.read()
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "prompt.md")
    if os.path.isfile(default):
        with open(default) as f:
            return f.read()
    return SYSTEM_PROMPT


def run(task, chat, tools, system_prompt=None, max_steps=25, on_step=None,
        messages=None, usage_cb=None):
    """the loop. chat(messages) -> assistant message dict, tools is a ToolSet."""
    if messages is None:
        messages = [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
    elif task:
        # resuming: the new task goes on top of the old history
        messages = list(messages) + [{"role": "user", "content": task}]
    else:
        messages = list(messages)

    for step in range(max_steps):
        reply = chat(messages, tools.schemas())
        if usage_cb:
            usage_cb(step + 1, reply.get("usage"))
        messages.append(_clean_reply(reply))

        calls = reply.get("tool_calls") or []
        if not calls:
            return messages  # model is done talking, no tools wanted

        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
                result = "error: arguments were not valid json"
            else:
                result = tools.call(name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": str(result),
            })

        if on_step:
            on_step(step + 1, calls)

    messages.append({
        "role": "assistant",
        "content": "stopped: hit max steps (%d)" % max_steps,
    })
    return messages


def _clean_reply(reply):
    # keep only what the api needs back
    msg = {"role": "assistant"}
    if reply.get("content"):
        msg["content"] = reply["content"]
    if reply.get("tool_calls"):
        msg["tool_calls"] = reply["tool_calls"]
    return msg


def last_answer(messages):
    for m in reversed(messages):
        if m["role"] == "assistant" and m.get("content"):
            return m["content"]
    return ""
