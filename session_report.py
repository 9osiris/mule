"""render, diff, and cost saved sessions, plus run logs and templates."""
import datetime
import difflib
import html
import json
import os

import sessions
from cost import cost_for, fmt_cost


def run_log_dir():
    # local-only run log lives here, never sent anywhere
    d = os.path.expanduser("~/.mule")
    os.makedirs(d, exist_ok=True)
    return d


def templates_dir():
    d = os.path.expanduser("~/.mule/templates")
    os.makedirs(d, exist_ok=True)
    return d


def _task_of(messages):
    # first user message is the task
    for m in messages:
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _final_answer(messages):
    # last non-empty assistant message is the answer
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return str(m["content"])
    return ""


def _usage_totals(messages):
    # sum per-message usage dicts when present, else no figures
    pin, pout, model = 0, 0, None
    seen = False
    for m in messages:
        usage = m.get("usage")
        if isinstance(usage, dict):
            seen = True
            pin += usage.get("prompt_tokens") or 0
            pout += usage.get("completion_tokens") or 0
        if model is None and isinstance(m.get("model"), str):
            model = m["model"]
    if not seen:
        return None, None, model
    return pin, pout, model


def _tool_call_text(tc):
    # one readable line per tool call
    fn = tc.get("function") or {}
    return "%s %s" % (fn.get("name"), fn.get("arguments") or "")


def export_markdown(name):
    # readable markdown transcript: task, turns, tool calls with
    # outcomes, final answer, and a cost section when usage exists
    messages = sessions.load_session(name)
    lines = ["# mule session: %s" % name, ""]
    task = _task_of(messages)
    if task:
        lines += ["## task", "", task, ""]
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "user":
            lines += ["## user", "", str(m.get("content") or ""), ""]
        elif role == "assistant":
            lines += ["## assistant", ""]
            if m.get("content"):
                lines += [str(m["content"]), ""]
            for tc in m.get("tool_calls") or []:
                lines += ["```", _tool_call_text(tc), "```", ""]
        elif role == "tool":
            lines += ["> tool result", "", "```",
                      str(m.get("content") or ""), "```", ""]
    answer = _final_answer(messages)
    if answer:
        lines += ["## final answer", "", answer, ""]
    pin, pout, model = _usage_totals(messages)
    if pin is not None:
        cost = cost_for(model, pin, pout) if model else None
        lines += ["## cost", "",
                  "tokens: %s in / %s out" % ("{:,}".format(pin),
                                              "{:,}".format(pout)),
                  "model: %s" % (model or "unknown"),
                  "cost: %s" % fmt_cost(cost), ""]
    return "\n".join(lines).rstrip() + "\n"


_CSS = ("body{font-family:sans-serif;max-width:800px;margin:2em auto;"
        "padding:0 1em;color:#1d1d1f;background:#fff}"
        "h1{font-size:1.5em}h2{font-size:1.2em;margin-top:1.5em}"
        "pre{background:#f5f5f7;padding:0.8em;overflow-x:auto;"
        "white-space:pre-wrap;word-wrap:break-word}"
        ".turn{border-left:3px solid #ddd;padding-left:1em;"
        "margin-bottom:1em}.user{border-color:#0071e3}"
        ".assistant{border-color:#34c759}.tool{border-color:#888}"
        ".final{border-color:#000}.cost{color:#666}")


def export_html(name):
    # self-contained html, inline css only, no external assets
    messages = sessions.load_session(name)
    parts = ["<!DOCTYPE html>", "<html>", "<head>",
             '<meta charset="utf-8">',
             "<title>mule session: %s</title>" % html.escape(name),
             "<style>%s</style>" % _CSS, "</head>", "<body>",
             "<h1>mule session: %s</h1>" % html.escape(name)]
    task = _task_of(messages)
    if task:
        parts += ["<h2>task</h2>", "<p>%s</p>" % html.escape(task)]
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "user":
            parts += ['<div class="turn user"><h2>user</h2>',
                      "<p>%s</p>" % html.escape(str(m.get("content") or "")),
                      "</div>"]
        elif role == "assistant":
            parts += ['<div class="turn assistant"><h2>assistant</h2>']
            if m.get("content"):
                parts += ["<p>%s</p>" % html.escape(str(m["content"]))]
            for tc in m.get("tool_calls") or []:
                parts += ["<pre>%s</pre>"
                          % html.escape(_tool_call_text(tc))]
            parts += ["</div>"]
        elif role == "tool":
            parts += ['<div class="turn tool"><h2>tool result</h2>',
                      "<pre>%s</pre>"
                      % html.escape(str(m.get("content") or "")),
                      "</div>"]
    answer = _final_answer(messages)
    if answer:
        parts += ['<div class="turn final"><h2>final answer</h2>',
                  "<p>%s</p>" % html.escape(answer), "</div>"]
    pin, pout, model = _usage_totals(messages)
    if pin is not None:
        cost = cost_for(model, pin, pout) if model else None
        parts += ['<div class="cost"><h2>cost</h2>',
                  "<p>tokens: %s in / %s out</p>"
                  % ("{:,}".format(pin), "{:,}".format(pout)),
                  "<p>model: %s</p>" % html.escape(model or "unknown"),
                  "<p>cost: %s</p>" % html.escape(fmt_cost(cost)),
                  "</div>"]
    parts += ["</body>", "</html>"]
    return "\n".join(parts) + "\n"


def _message_lines(messages):
    # one block of lines per message, for diffing
    lines = []
    for i, m in enumerate(messages):
        role = m.get("role") or "unknown"
        lines.append("turn %d [%s]" % (i + 1, role))
        content = str(m.get("content") or "")
        lines.extend(content.splitlines() or [""])
        for tc in m.get("tool_calls") or []:
            lines.append("tool call: " + _tool_call_text(tc))
        lines.append("")
    return lines


def diff_sessions(a, b):
    # unified-style diff of two sessions message histories
    ma = sessions.load_session(a)
    mb = sessions.load_session(b)
    diff = difflib.unified_diff(
        _message_lines(ma), _message_lines(mb),
        fromfile="session: " + a, tofile="session: " + b,
        lineterm="")
    text = "\n".join(diff)
    if not text.strip():
        return "no differences between %s and %s\n" % (a, b)
    return text + "\n"


def _session_cost(name):
    # counts always, token and cost figures only when usage exists
    messages = sessions.load_session(name)
    tool_calls = sum(len(m.get("tool_calls") or [])
                     for m in messages if m.get("role") == "assistant")
    pin, pout, model = _usage_totals(messages)
    cost = None
    if pin is not None and model:
        cost = cost_for(model, pin, pout)
    return {"name": name, "messages": len(messages),
            "tool_calls": tool_calls,
            "prompt_tokens": pin, "completion_tokens": pout,
            "model": model, "cost": cost,
            "has_usage": pin is not None}


def cost_report(names=None):
    # per-session and total cost summary, no fabricated figures
    if names is None:
        names = sessions.list_sessions()
    rows = [_session_cost(n) for n in names]
    total = {"sessions": len(rows),
             "messages": sum(r["messages"] for r in rows),
             "tool_calls": sum(r["tool_calls"] for r in rows),
             "prompt_tokens": None, "completion_tokens": None,
             "cost": None}
    if any(r["has_usage"] for r in rows):
        total["prompt_tokens"] = sum(r["prompt_tokens"] or 0 for r in rows)
        total["completion_tokens"] = sum(r["completion_tokens"] or 0
                                         for r in rows)
    known = [r["cost"] for r in rows if r["cost"] is not None]
    if known:
        total["cost"] = sum(known)
    return {"sessions": rows, "total": total}


def _run_log_path(log_dir=None):
    d = log_dir or run_log_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "run_log.jsonl")


def append_run_log(entry, log_dir=None):
    # one jsonl line per run, local file only, no network involved
    if not isinstance(entry, dict):
        raise ValueError("run log entry must be a dict")
    record = dict(entry)
    record.setdefault("timestamp",
                      datetime.datetime.now(datetime.timezone.utc)
                      .isoformat())
    path = _run_log_path(log_dir)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return path


def read_run_log(log_dir=None):
    # read the local run log back, empty list when there is none
    path = _run_log_path(log_dir)
    if not os.path.isfile(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def _template_path(name, template_dir=None):
    # plain json file under the templates dir, no path escapes
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("bad template name: %r" % name)
    filename = name if name.endswith(".json") else name + ".json"
    d = template_dir or templates_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, filename)


def save_template(name, messages, template_dir=None):
    # store a starter message list for a new run
    path = _template_path(name, template_dir)
    with open(path, "w") as f:
        json.dump(list(messages), f)
    return path


def start_from_template(name, template_dir=None):
    # starter messages for a new run, or a clean error if missing
    path = _template_path(name, template_dir)
    if not os.path.isfile(path):
        raise ValueError("no such template: %s" % name)
    with open(path) as f:
        messages = json.load(f)
    if not isinstance(messages, list):
        raise ValueError("template %s is not a message list" % name)
    return messages


def list_templates(template_dir=None):
    d = template_dir or templates_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
