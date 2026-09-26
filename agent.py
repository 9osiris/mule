import json
import os


SYSTEM_PROMPT = """You are a coding agent working inside a project directory.
You have tools to read files, write files, edit files, list directories,
run shell commands, fetch web pages, and search the web.
web_search takes a query and returns titles, urls, and snippets.
todo_write and todo_read track a todo list for multi-step work.
read_image loads a png/jpg/gif/webp as a base64 data uri.
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


def plan_and_approve(task, chat, decide, system_prompt=None,
                     messages=None):
    """ask the model for a plan first. decide(plan) -> "y"/"n"/"r".

    returns (messages, approved). approved is False when the user
    rejects or burns their one revision without approving."""
    if messages is None:
        messages = [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        ]
    else:
        messages = list(messages)
    messages.append({
        "role": "user",
        "content": task + "\n\nfirst, write a short plan: the steps you "
                   "will take. do not call any tools yet.",
    })
    revised = False
    while True:
        reply = chat(messages, [])
        plan = (reply.get("content") or "").strip()
        messages.append(_clean_reply(reply))
        answer = decide(plan)
        if answer == "y":
            return messages, True
        if answer == "r" and not revised:
            revised = True
            messages.append({
                "role": "user",
                "content": "revise the plan and try again.",
            })
            continue
        return messages, False


def context_size(messages):
    # rough char count of the whole history
    return len(json.dumps(messages))


def compact_messages(messages, chat, keep_last=10):
    # squash the oldest messages into one summary, keep the
    # system prompt and the recent tail intact.
    # returns (new_messages, summary_usage) so the summary call
    # still counts toward cost tracking.
    head = 1 if (messages and messages[0].get("role") == "system") else 0
    if len(messages) <= head + keep_last + 1:
        return messages, None
    middle = messages[head:-keep_last]
    tail = messages[-keep_last:]
    reply = chat([
        {"role": "system",
         "content": "summarize the conversation below in a few sentences. "
                    "keep file names, decisions made, and anything unfinished."},
        {"role": "user", "content": json.dumps(middle)},
    ], [])
    summary = (reply.get("content") or "").strip() or "(no summary)"
    out = messages[:head]
    out.append({"role": "user",
                "content": "[earlier context summarized]\n" + summary})
    out.extend(tail)
    return out, reply.get("usage")


def run(task, chat, tools, system_prompt=None, max_steps=25, on_step=None,
        messages=None, usage_cb=None, on_todos=None, context_budget=None,
        dry_run=False):
    """the loop. chat(messages) -> assistant message dict, tools is a ToolSet.
    dry_run prints what would happen without executing any tool."""
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

    last_todos = None

    for step in range(max_steps):
        if (context_budget and context_size(messages) > context_budget
                and len(messages) > 12):
            # history got too big, squash the old stuff down.
            # the summary call still counts toward cost tracking
            messages, summary_usage = compact_messages(messages, chat)
            if usage_cb and summary_usage:
                usage_cb(step + 1, summary_usage)

        reply = chat(messages, tools.schemas())
        stop = usage_cb(step + 1, reply.get("usage")) if usage_cb else False
        messages.append(_clean_reply(reply))
        if stop:
            # usage_cb asked to stop, e.g. the cost budget ran out
            messages.append({
                "role": "assistant",
                "content": "stopped: hit cost budget",
            })
            return messages

        calls = reply.get("tool_calls") or []
        if not calls:
            return messages  # model is done talking, no tools wanted

        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
                bad_args = False
            except json.JSONDecodeError:
                args = {}
                bad_args = True
            if bad_args:
                result = "error: arguments were not valid json"
            elif dry_run:
                # show the plan, touch nothing
                result = "dry run, not executed: %s(%s)" % (
                    name, json.dumps(args, sort_keys=True))
            else:
                result = tools.call(name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": str(result),
            })

        if on_todos and hasattr(tools, "progress_line"):
            line = tools.progress_line()
            if line and line != last_todos:
                last_todos = line
                on_todos(line)

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
