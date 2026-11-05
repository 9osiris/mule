"""system prompts from files, plus {{var}} template rendering."""
import os
import re

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def load_system_file(path):
    # read a system prompt file, stripped; missing file is an error
    if not path or not os.path.isfile(path):
        raise ValueError("no such system prompt file: %s" % path)
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError as e:
        raise ValueError("cannot read system prompt file %s: %s"
                         % (path, e))


def render_template(text, vars=None):
    # replace {{name}} with vars[name], unknown placeholders stay put
    if not text or not vars:
        return text or ""
    def _sub(match):
        name = match.group(1)
        if name not in vars:
            return match.group(0)
        value = vars[name]
        return str(value) if value is not None else ""
    return _TEMPLATE_RE.sub(_sub, text)


def list_templates(dir=None):
    # template files in a dir, sorted by name, no subdirs
    if not dir or not os.path.isdir(dir):
        return []
    try:
        names = os.listdir(dir)
    except OSError:
        return []
    return sorted(f for f in names
                  if os.path.isfile(os.path.join(dir, f)))
