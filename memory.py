"""persistent project memory, one MEMORY.md file per root."""
import datetime
import os

MEMORY_FILE = "MEMORY.md"
MAX_MEMORY_CHARS = 20000


def _path(root):
    # memory file path under the project root
    return os.path.join(str(root), MEMORY_FILE)


def load_memory(root):
    # whole memory file as text, empty string when absent
    try:
        with open(_path(root)) as f:
            return f.read()
    except OSError:
        return ""


def append_memory(root, fact, max_chars=MAX_MEMORY_CHARS):
    # append one dated fact line, creating the file if needed.
    # drops the oldest lines when the file would exceed the cap.
    fact = str(fact).strip()
    if not fact:
        return ""
    line = "- %s: %s\n" % (datetime.date.today().isoformat(), fact)
    root_str = str(root)
    try:
        os.makedirs(root_str, exist_ok=True)
    except OSError:
        pass
    text = load_memory(root)
    lines = text.splitlines(True) if text else []
    lines.append(line)
    merged = "".join(lines)
    while len(merged) > max_chars and len(lines) > 1:
        lines.pop(0)
        merged = "".join(lines)
    with open(_path(root), "w") as f:
        f.write(merged)
    return line


def memory_context(root, budget):
    # tail of the memory file within budget chars
    text = load_memory(root)
    if budget is None:
        return text
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        return text
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    cut = len(text) - budget
    idx = text.find("\n", cut)
    if idx == -1 or idx + 1 >= len(text):
        return text[cut:]
    return text[idx + 1:]
