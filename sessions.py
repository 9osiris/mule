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
    return "session-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


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
