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


def _branch_path(name):
    # sidecar next to the session file, records the parent
    path = _path(name)
    return path[:-6] + ".branch"


def branch_session(name, parent):
    # fork a saved session: same history, new name, and a sidecar
    # so listings can show the relationship. the parent is never
    # touched, later turns in the branch stay in the branch.
    if name == parent:
        raise ValueError("cannot branch a session onto itself")
    if not os.path.isfile(_path(parent)):
        raise ValueError("no such session: %s" % parent)
    if os.path.isfile(_path(name)):
        raise ValueError("session already exists: %s" % name)
    messages = load_session(parent)
    save_session(name, messages)
    with open(_branch_path(name), "w") as f:
        json.dump({"parent": parent}, f)
    return _path(name)


def branch_parent(name):
    # parent session name, or None when this is not a branch
    try:
        with open(_branch_path(name)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        return data.get("parent")
    return None


def _fork_meta_path(name):
    # sidecar next to the session file, records the fork parent
    # and the step it was cut at. the session file itself stays
    # plain jsonl, one message per line, like every other session.
    path = _path(name)
    return path[:-6] + ".meta.json"


def fork_at_step(name, new_name, step_index):
    # copy a saved session truncated to step_index messages into
    # a new named session. the parent name and step are recorded
    # in a <new_name>.meta.json sidecar next to the new file.
    # loading the fork returns the truncated history.
    if new_name == name:
        raise ValueError("cannot fork a session onto itself")
    if not os.path.isfile(_path(name)):
        raise ValueError("no such session: %s" % name)
    if os.path.isfile(_path(new_name)):
        raise ValueError("session already exists: %s" % new_name)
    if isinstance(step_index, bool) or not isinstance(step_index, int):
        raise ValueError("step_index must be an integer")
    messages = load_session(name)
    if step_index < 0 or step_index > len(messages):
        raise ValueError("step_index %d out of range for %d messages"
                         % (step_index, len(messages)))
    save_session(new_name, messages[:step_index])
    with open(_fork_meta_path(new_name), "w") as f:
        json.dump({"parent": name, "step": step_index,
                   "kind": "fork"}, f)
    return _path(new_name)


def fork_info(name):
    # fork metadata for a session, or None when it is not a fork
    try:
        with open(_fork_meta_path(name)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("kind") == "fork":
        return data
    return None


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
