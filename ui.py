"""tiny ansi color helpers. off with --no-color or NO_COLOR set."""
import os

_enabled = not os.environ.get("NO_COLOR")


def init_color(no_color):
    # call once from main. the flag wins, then the env var.
    global _enabled
    _enabled = not (no_color or os.environ.get("NO_COLOR"))


def _wrap(code, text):
    if not _enabled or not text:
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def red(text):
    return _wrap("31", text)


def green(text):
    return _wrap("32", text)


def yellow(text):
    return _wrap("33", text)


def dim(text):
    return _wrap("2", text)


def color_diff(diff):
    # unified diff: added lines green, removed red, headers dim
    out = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("+"):
            out.append(green(line))
        elif line.startswith("-"):
            out.append(red(line))
        elif line.startswith("@@"):
            out.append(dim(line))
        else:
            out.append(line)
    return "".join(out)
