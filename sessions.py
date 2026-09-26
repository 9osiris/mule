"""save and resume agent conversations as jsonl session files."""
import datetime
import json
import os


def session_dir():
    d = os.path.expanduser("~/.mule/sessions")
    os.makedirs(d, exist_ok=True)
    return d


def _path(name):
    if not name.endswith(".jsonl"):
        name += ".jsonl"
    return os.path.join(session_dir(), name)


def auto_name():
    # timestamp name, with a counter when two runs share a second
    base = "session-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name, i = base, 2
    while os.path.isfile(_path(name)):
        name = "%s-%d" % (base, i)
        i += 1
    return name


def save_session(name, messages):
    # one message per line, plain json
    path = _path(name)
    with open(path, "w") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")
    return path


def load_session(name):
    # returns the message list, raises a clean error if missing
    path = _path(name)
    if not os.path.isfile(path):
        raise ValueError("no such session: %s" % name)
    messages = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return messages


def list_sessions():
    d = session_dir()
    return sorted(f[:-6] for f in os.listdir(d) if f.endswith(".jsonl"))


def latest_session():
    # name of the most recently modified session, None when empty
    d = session_dir()
    paths = [os.path.join(d, f) for f in os.listdir(d)
             if f.endswith(".jsonl")]
    if not paths:
        return None
    newest = max(paths, key=os.path.getmtime)
    return os.path.basename(newest)[:-6]


def search_sessions(query):
    # find saved sessions containing the query, with a snippet each
    q = (query or "").lower()
    hits = []
    for name in list_sessions():
        try:
            messages = load_session(name)
        except (ValueError, OSError):
            continue
        for m in messages:
            text = str(m.get("content") or "")
            if q in text.lower():
                i = text.lower().index(q)
                snippet = text[max(0, i - 40):i + 80].replace("\n", " ")
                hits.append((name, snippet.strip()))
                break
    return hits


def rename_session(old, new):
    # rename a saved session file
    src, dst = _path(old), _path(new)
    if not os.path.isfile(src):
        raise ValueError("no such session: %s" % old)
    if os.path.isfile(dst):
        raise ValueError("session already exists: %s" % new)
    os.rename(src, dst)
    return dst


def delete_session(name):
    # remove a saved session file
    path = _path(name)
    if not os.path.isfile(path):
        raise ValueError("no such session: %s" % name)
    os.remove(path)
    return path


def session_stats():
    # per-session message counts and file sizes
    stats = []
    for name in list_sessions():
        path = _path(name)
        count = 0
        try:
            with open(path) as f:
                for line in f:
                    if line.strip():
                        count += 1
        except OSError:
            continue
        stats.append({"name": name, "messages": count,
                      "bytes": os.path.getsize(path)})
    return stats


def import_history(path):
    # start from an old conversation: .jsonl session files load
    # directly, .md transcripts (from --export) parse back into
    # user/assistant messages. tool blocks don't round-trip.
    if path.endswith(".jsonl"):
        messages = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
        return messages
    if path.endswith(".md"):
        return _import_markdown(path)
    raise ValueError("cannot import %s: use a .md or .jsonl file" % path)


def _import_markdown(path):
    messages = []
    role, buf, in_fence = None, [], False

    def flush():
        text = "\n".join(buf).strip()
        if role and text:
            messages.append({"role": role, "content": text})

    with open(path) as f:
        for line in f:
            s = line.rstrip("\n")
            if s.startswith("```"):
                if in_fence:
                    flush()
                    role, buf = None, []
                in_fence = not in_fence
                continue
            if in_fence:
                continue  # tool calls don't round-trip
            if s == "## user":
                flush()
                role, buf = "user", []
            elif s == "## assistant":
                flush()
                role, buf = "assistant", []
            elif s.startswith("#") or s.startswith(">"):
                continue
            else:
                buf.append(s)
    flush()
    return messages


def export_session(path, messages, cost_line=None):
    # readable markdown transcript: turns, tool calls, cost at the bottom
    lines = ["# mule session", ""]
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
                fn = tc.get("function") or {}
                lines += ["```",
                          "%s %s" % (fn.get("name"),
                                     fn.get("arguments") or ""),
                          "```", ""]
        elif role == "tool":
            lines += ["> tool result", "", "```",
                      str(m.get("content") or ""), "```", ""]
    if cost_line:
        lines += ["## cost", "", cost_line, ""]
    with open(path, "w") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return path
