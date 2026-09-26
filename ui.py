"""tiny ansi color helpers. off with --no-color or NO_COLOR set."""
import os
import sys

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


def brand(text):
    # bold amber, the mule color: the mark, panels, the footer rule
    return _wrap("1;33", text)


def cyan(text):
    return _wrap("36", text)


MULE_HEAD = r"""   /\ /\
  / \ / \
  \ \__/ /
   \ ____ /
    \______/"""


def banner_text(version, model):
    # the startup banner: the mark, then version and model
    return "%s\n  mule %s - model %s" % (brand(MULE_HEAD), version,
                                         model)


def tool_panel(name, summary):
    # one tool call as a bordered panel, name in cyan
    lines = [brand("┌─ ") + cyan(name)]
    if summary:
        lines.append("│ " + summary)
    lines.append(brand("└─"))
    return "\n".join(lines)


def status_footer(version, model, tokens_in, tokens_out, cost):
    # the end-of-run footer: model, tokens, cost under an amber rule
    return "%s\nmule %s | model %s | %s in / %s out | %s" % (
        brand("─" * 40), version, model,
        "{:,}".format(tokens_in), "{:,}".format(tokens_out), cost)


SPINNER_FRAMES = ["◷", "◶", "◵", "◴"]


class Spinner:
    # one-line spinner while waiting on the model. silent unless
    # stdout is a real terminal, cleared when the reply lands.
    def __init__(self, label="thinking"):
        self.label = label
        self._i = 0
        self._on = False

    def _ok(self):
        return (_enabled and hasattr(sys.stdout, "isatty")
                and sys.stdout.isatty())

    def tick(self):
        if not self._ok():
            return
        frame = SPINNER_FRAMES[self._i % len(SPINNER_FRAMES)]
        sys.stdout.write("\r%s %s" % (brand(frame), self.label))
        sys.stdout.flush()
        self._i += 1
        self._on = True

    def done(self):
        if self._on and self._ok():
            sys.stdout.write("\r" + " " * (len(self.label) + 4) + "\r")
            sys.stdout.flush()
        self._on = False


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
