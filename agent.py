import json
import os
import threading
import time


SYSTEM_PROMPT = """You are a coding agent working inside a project directory.
You have tools to read files, write files, edit files, list directories,
run shell commands, fetch web pages, and search the web.
web_search takes a query and returns titles, urls, and snippets.
todo_write and todo_read track a todo list for multi-step work.
read_image loads a png/jpg/gif/webp as a base64 data uri.
delegate hands a subtask to a subagent with its own history and returns
a summary. delegates cannot delegate further.
run_shell with background=true starts a background job; jobs lists them,
job_output reads one, job_kill stops one.
ask_user asks the human a question with 2-4 options, but only works in
interactive or --ask mode. otherwise make your best guess and say so.
All paths are relative to the project root. Use them for everything.

Rules:
- Read before you change. Look at the files involved first.
- Narrate as you go: before each batch of tool calls, say in one
  short line what you are about to do, so the human can follow along.
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
        dry_run=False, max_tools=None, time_limit=None, on_timing=None,
        parallel_tools=False, tool_timeout=None):
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
    tool_count = 0
    retried = set()  # tool call ids that already got one guided retry
    deadline = (time.monotonic() + time_limit
                if time_limit is not None else None)

    def _exec(call):
        # run one tool call, returns the result string
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
            bad_args = False
        except json.JSONDecodeError:
            args = {}
            bad_args = True
        if bad_args:
            return "error: arguments were not valid json"
        if dry_run:
            # show the plan, touch nothing
            return "dry run, not executed: %s(%s)" % (
                name, json.dumps(args, sort_keys=True))
        if tool_timeout is None:
            return tools.call(name, args)
        # slow tools get cut off, not waited on forever.
        # the thread is a daemon, so a stuck tool can't
        # hold the process open after mule exits.
        import queue as queue_mod
        q = queue_mod.Queue()

        def _target():
            try:
                q.put(tools.call(name, args))
            except Exception as e:  # tools.call stringifies, this is backup
                q.put("error: %s" % e)

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        try:
            return q.get(timeout=tool_timeout)
        except queue_mod.Empty:
            return "error: tool '%s' timed out after %ss" % (
                name, tool_timeout)

    for step in range(max_steps):
        if deadline is not None and time.monotonic() >= deadline:
            messages.append({
                "role": "assistant",
                "content": "stopped: hit time limit (%ss)" % time_limit,
            })
            return messages
        if (context_budget and context_size(messages) > context_budget
                and len(messages) > 12):
            # history got too big, squash the old stuff down.
            # the summary call still counts toward cost tracking
            messages, summary_usage = compact_messages(messages, chat)
            if usage_cb and summary_usage:
                usage_cb(step + 1, summary_usage)

        t0 = time.monotonic()
        reply = chat(messages, tools.schemas())
        if on_timing:
            on_timing(step + 1, time.monotonic() - t0)
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

        if max_tools is not None and tool_count >= max_tools:
            messages.append({
                "role": "assistant",
                "content": "stopped: hit max tools (%d)" % max_tools,
            })
            return messages

        runnable = calls
        if (max_tools is not None
                and tool_count + len(calls) > max_tools):
            # run what fits under the cap, the rest get an error
            # result so every tool_call still has a matching result
            runnable = calls[:max_tools - tool_count]

        if parallel_tools and len(runnable) > 1 and not dry_run:
            # independent calls go out together, results stay ordered
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4) as pool:
                ran = list(pool.map(_exec, runnable))
        else:
            ran = [_exec(c) for c in runnable]
        tool_count += len(runnable)
        results = ran + ["error: hit max tools (%d), call not executed"
                         % max_tools for _ in calls[len(runnable):]]

        for call, result in zip(calls, results):
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": str(result),
            })

        # retry nudges go after every result, never between them:
        # some gateways require each tool call to sit right next
        # to its result with nothing in between
        failed = [(c, r) for c, r in zip(calls, results)
                  if str(r).startswith("error:")
                  and c["id"] not in retried]
        if failed:
            for call, _ in failed:
                retried.add(call["id"])
            messages.append({
                "role": "user",
                "content": "these tool calls failed: %s. fix the "
                           "arguments and try once more, or move on."
                           % "; ".join("%s: %s" % (c["function"]["name"], r)
                                       for c, r in failed),
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
    # keep only what the api needs back. content is always present:
    # some gateways reject assistant messages that carry tool calls
    # but no content field at all.
    msg = {"role": "assistant", "content": reply.get("content") or ""}
    if reply.get("tool_calls"):
        msg["tool_calls"] = reply["tool_calls"]
    return msg


def last_answer(messages):
    for m in reversed(messages):
        if m["role"] == "assistant" and m.get("content"):
            return m["content"]
    return ""
