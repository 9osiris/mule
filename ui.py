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


def tool_panel(name, summary):
    # one tool call as a rounded panel, name in the title bar.
    # plain text when color is off, so widths stay exact.
    rows = [r for r in str(summary or "").splitlines()]
    inner = max([len(name)] + [len(r) for r in rows] + [0])
    w = min(70, max(inner + 4, len(name) + 6, 20))
    top = "╭─ " + name + " " + "─" * (w - 5 - len(name)) + "╮"
    out = [brand(top[:3]) + cyan(name)
           + brand(top[3 + len(name):])]
    for r in rows:
        cell = r[:w - 4] + " " * (w - 4 - len(r[:w - 4]))
        out.append(brand("│ ") + cell + brand(" │"))
    out.append(brand("╰" + "─" * (w - 2) + "╯"))
    return "\n".join(out)


def run_banner(version, model, root):
    # non-interactive startup: the mark, then version/model/root
    return "%s\n  %s" % (
        brand(MULE_HEAD),
        dim("mule %s · %s · %s" % (version, model, root)))


def step_line(step, tokens_in, tokens_out, cost):
    # one dim line per step: number, tokens, cost
    return dim("  step %d · %s in / %s out · %s" % (
        step, "{:,}".format(tokens_in),
        "{:,}".format(tokens_out), cost))


def _cell(text, width):
    # pad or cut one column cell, plain text only
    text = str(text)[:width]
    return text + " " * (width - len(text))


def welcome_screen(version, model, root, recent, tips):
    # repl welcome, claude-code style: banner and model on the
    # left, recent sessions and tips stacked on the right,
    # all inside one dashed border. color is applied to whole
    # padded cells so the columns stay aligned.
    w, lw, rw = 78, 33, 39
    title = " mule v%s " % version
    dash = w - 2 - len(title)
    lines = [brand("┄" * (dash // 2) + title + "┄" * (dash - dash // 2))]
    left = ["", "welcome back.", ""] + MULE_HEAD.splitlines()
    left += ["", "model  " + model, "cwd    " + root]
    right = ["recent activity"]
    right += [r for r in list(recent)[:4]] or ["(no sessions yet)"]
    right += ["... /sessions for more", "", "tips"]
    right += list(tips)[:3]
    for i in range(max(len(left), len(right))):
        l = _cell(left[i] if i < len(left) else "", lw)
        r = _cell(right[i] if i < len(right) else "", rw)
        if 3 <= i < 3 + len(MULE_HEAD.splitlines()):
            l = brand(l)
        rt = right[i] if i < len(right) else ""
        if rt in ("recent activity", "tips"):
            r = brand(r)
        lines.append("  " + l + "  " + r)
    lines.append(brand("┄" * 76))
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
