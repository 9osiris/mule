"""terminal rendering: colors, markdown, tool transcript, spinners.

stdlib at the core. when rich is installed, RichRenderer gives
markdown, syntax highlighting, and live streaming. otherwise
PlainRenderer does a decent job with plain ansi.
pip install rich (or pip install mule[rich]) to upgrade.
"""
import os
import re
import sys
import threading

_enabled = not os.environ.get("NO_COLOR")

# rich is optional. one guarded import, one flag, checked once.
# nothing below imports rich outside this block.
try:
    from rich.console import Console as _RichConsole
    from rich.markdown import Markdown as _RichMarkdown
    from rich.live import Live as _RichLive
    _RICH = True
except ImportError:
    _RICH = False


def _windows_ansi():
    # windows 10+: ask the console to interpret ansi escapes.
    # true when colors will actually render on this terminal.
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 4)
            return True
    except Exception:
        pass
    return False


def init_color(no_color):
    # call once from main. the flag wins, then the env var.
    # on windows without working ansi, color turns off entirely,
    # so escapes never print as raw text.
    global _enabled
    _enabled = not (no_color or os.environ.get("NO_COLOR"))
    if _enabled and not _windows_ansi():
        _enabled = False
    refresh_glyphs()


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


def bold(text):
    return _wrap("1", text)


def dim(text):
    return _wrap("2", text)


def brand(text):
    # bold amber, the mule color: the mark, panels, the footer rule
    return _wrap("1;33", text)


def cyan(text):
    return _wrap("36", text)


# glyphs: unicode when the terminal can show it, plain ascii on
# legacy consoles (windows cmd) where fancy chars print as boxes.
def _supports_unicode():
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "\u23fa\u23bf\u276f\u2500\u00b7\u25f7".encode(enc)
        return True
    except Exception:
        return False


UNICODE_OK = _supports_unicode()


def _glyphs():
    if UNICODE_OK:
        return {"call": "\u23fa", "result": "\u23bf",
                "rule": "\u2500", "thin": "\u2504",
                "prompt": "\u276f", "dot": "\u00b7",
                "frames": ["\u25f7", "\u25f6", "\u25f5", "\u25f4"],
                "tl": "\u256d", "tr": "\u256e",
                "bl": "\u2570", "br": "\u256f",
                "v": "\u2502"}
    return {"call": "*", "result": "|",
            "rule": "-", "thin": "-",
            "prompt": ">", "dot": "|",
            "frames": ["|", "/", "-", "\\"],
            "tl": "+", "tr": "+",
            "bl": "+", "br": "+",
            "v": "|"}


G = _glyphs()


def refresh_glyphs(force=None):
    # re-detect, in case stdout changed since import. updates the
    # dict in place so existing imports keep working. force=True
    # or False pins the set, which is what tests use.
    global UNICODE_OK
    UNICODE_OK = _supports_unicode() if force is None else force
    G.clear()
    G.update(_glyphs())


MULE_HEAD = r"""   /\ /\
  / \ / \
  \ \__/ /
   \ ____ /
    \______/"""


# plain markdown rendering, stdlib only

def _inline_md(text):
    # `code` spans are stashed first so * inside them is untouched.
    # then **bold**, then *italic* / _italic_. degrades to plain
    # text when color is off.
    codes = []

    def _stash(m):
        codes.append(cyan(m.group(1)))
        return "\x00%d\x00" % (len(codes) - 1)

    text = re.sub(r"`([^`\n]+)`", _stash, text)
    text = re.sub(r"\*\*([^*]+)\*\*", lambda m: bold(m.group(1)), text)
    text = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)",
                  lambda m: dim(m.group(1)), text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)",
                  lambda m: dim(m.group(1)), text)
    for i, c in enumerate(codes):
        text = text.replace("\x00%d\x00" % i, c)
    return text


def render_markdown_plain(text):
    # line-based markdown for terminals without rich: headings,
    # rules, quotes, fenced code, and inline styles. anything
    # unrecognized passes through untouched.
    out = []
    in_fence = False
    for line in str(text).splitlines():
        s = line.strip()
        if s.startswith("```"):
            if not in_fence and s[3:].strip():
                out.append(dim("  -- %s --" % s[3:].strip()))
            in_fence = not in_fence
            continue
        if in_fence:
            out.append("  " + line)
            continue
        if re.match(r"^#{1,6}\s", s):
            out.append(brand(bold(re.sub(r"^#{1,6}\s+", "", s))))
        elif s in ("---", "***", "___"):
            out.append(dim("-" * 40))
        elif s.startswith(">"):
            out.append(dim(s))
        else:
            out.append(_inline_md(line))
    return "\n".join(out)


# tool transcript, one line per call

def args_summary(args):
    # {"path": ".", "x": 1} -> 'path=., x=1', values cut at 60
    return ", ".join("%s=%s" % (k, str(v)[:60])
                     for k, v in (args or {}).items())


def tool_call_line(name, summary=""):
    # one dim line per call: the amber dot, the name, the args.
    # no panel per call, the transcript stays scannable.
    head = "%s %s" % (brand(G["call"]), name)
    if summary:
        one = " ".join(str(summary).split())
        if len(one) > 100:
            one = one[:97] + "..."
        head += " " + dim(one)
    return head


def tool_result_line(text):
    # nested under the call: first line dimmed, "+N lines" when
    # truncated. errors show red so failures jump out.
    text = str(text or "")
    lines = text.splitlines()
    first = lines[0][:120] if lines else "(empty)"
    extra = max(0, len(lines) - 1)
    s = G["result"] + " " + first
    if extra:
        s += " ... +%d lines" % extra
    if text.startswith("error:"):
        return red(s)
    return dim(s)


def tool_panel(name, summary):
    # one tool call as a rounded panel, name in the title bar.
    # kept for scripts that used it; the repl now uses the
    # compact transcript lines above instead.
    rows = [r for r in str(summary or "").splitlines()]
    inner = max([len(name)] + [len(r) for r in rows] + [0])
    w = min(70, max(inner + 4, len(name) + 6, 20))
    top = G["tl"] + G["rule"] + " " + name + " " \
        + G["rule"] * (w - 5 - len(name)) + G["tr"]
    out = [brand(top[:3]) + cyan(name)
           + brand(top[3 + len(name):])]
    for r in rows:
        cell = r[:w - 4] + " " * (w - 4 - len(r[:w - 4]))
        out.append(brand(G["v"] + " ") + cell + brand(" " + G["v"]))
    out.append(brand(G["bl"] + G["rule"] * (w - 2) + G["br"]))
    return "\n".join(out)


def run_banner(version, model, root):
    # non-interactive startup: the mark, then version/model/root
    d = G["dot"]
    return "%s\n  %s" % (
        brand(MULE_HEAD),
        dim("mule %s %s %s %s %s" % (version, d, model, d, root)))


def step_line(step, tokens_in, tokens_out, cost):
    # one dim line per step: number, tokens, cost
    d = G["dot"]
    return dim("  step %d %s %s in / %s out %s %s" % (
        step, d, "{:,}".format(tokens_in),
        "{:,}".format(tokens_out), d, cost))


def _cell(text, width):
    # pad or cut one column cell, plain text only
    text = str(text)[:width]
    return text + " " * (width - len(text))


def welcome_screen(version, model, root, recent, tips):
    # repl welcome: the mark and model on the left, recent
    # sessions and tips on the right. cells are padded as plain text first, then colorized whole, so the
    # columns stay aligned with color on or off.
    w, lw, rw, gap = 76, 32, 38, 4
    title = " mule v%s " % version
    fill = w - len(title)
    lines = [brand(G["thin"] * (fill // 2) + title
                   + G["thin"] * (fill - fill // 2))]
    head = MULE_HEAD.splitlines()
    left = (["", "welcome back.", ""]
            + head
            + ["", "model  " + str(model), "root   " + str(root)])
    right = (["recent activity"]
             + (list(recent)[:4] or ["(no sessions yet)"])
             + ["", "tips"]
             + list(tips)[:3])
    head_rows = set(range(3, 3 + len(head)))
    for i in range(max(len(left), len(right))):
        l = _cell(left[i] if i < len(left) else "", lw)
        r = _cell(right[i] if i < len(right) else "", rw)
        if i in head_rows:
            l = brand(l)
        if (right[i] if i < len(right) else "") in ("recent activity",
                                                    "tips"):
            r = brand(r)
        lines.append("  " + l + " " * gap + r)
    lines.append(brand(G["thin"] * w))
    return "\n".join(lines)


def status_footer(version, model, tokens_in, tokens_out, cost):
    # the end-of-run footer: model, tokens, cost under an amber rule
    return "%s\nmule %s | model %s | %s in / %s out | %s" % (
        brand(G["rule"] * 40), version, model,
        "{:,}".format(tokens_in), "{:,}".format(tokens_out), cost)


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

    def _frame(self):
        frames = G["frames"]
        return frames[self._i % len(frames)]

    def tick(self):
        if not self._ok():
            return
        frame = self._frame()
        sys.stdout.write("\r%s %s" % (brand(frame), self.label))
        sys.stdout.flush()
        self._i += 1
        self._on = True

    def done(self):
        if self._on and self._ok():
            sys.stdout.write("\r" + " " * (len(self.label) + 4) + "\r")
            sys.stdout.flush()
        self._on = False


class ThreadSpinner:
    # aider-style spinner: start() returns immediately, the first
    # frame appears after delay (fast replies never flicker),
    # stop() clears the line. runs on its own thread.
    def __init__(self, label="thinking", delay=0.5):
        self.label = label
        self.delay = delay
        self._stop = threading.Event()
        self._thread = None

    def _ok(self):
        return (_enabled and hasattr(sys.stdout, "isatty")
                and sys.stdout.isatty())

    def start(self):
        if not self._ok() or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        if self._stop.wait(self.delay):
            return
        i = 0
        frames = G["frames"]
        while not self._stop.is_set():
            frame = frames[i % len(frames)]
            sys.stdout.write("\r%s %s" % (brand(frame), self.label))
            sys.stdout.flush()
            i += 1
            if self._stop.wait(0.1):
                break
        sys.stdout.write("\r" + " " * (len(self.label) + 4) + "\r")
        sys.stdout.flush()

    def stop(self):
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1)
            self._thread = None


# renderers: plain stdlib vs rich

class _PlainStream:
    # no rich: tokens go straight to the terminal, raw.
    def feed(self, token):
        sys.stdout.write(token)
        sys.stdout.flush()

    def finish(self):
        sys.stdout.write("\n")
        sys.stdout.flush()


class Renderer:
    # plain stdlib rendering. subclass to upgrade.
    name = "plain"

    def print_markdown(self, text):
        print(render_markdown_plain(text))

    def stream(self):
        return _PlainStream()

    def tool_call(self, name, summary=""):
        print(tool_call_line(name, summary))

    def tool_result(self, text):
        print(tool_result_line(text))

    def rule(self, width=40):
        print(brand(G["rule"] * width))

    def welcome(self, version, model, root, recent, tips):
        print(welcome_screen(version, model, root, recent, tips))


class PlainRenderer(Renderer):
    name = "plain"


class RichRenderer(Renderer):
    # markdown, syntax highlighting, and live streaming via rich.
    # only constructed when the import above succeeded.
    name = "rich"

    def __init__(self):
        self.console = _RichConsole()

    def print_markdown(self, text):
        self.console.print(_RichMarkdown(str(text)))

    def stream(self):
        return _RichStream(self.console)


class _RichStream:
    # aider-style: re-render the accumulated markdown in a live
    # region as tokens arrive. the final frame stays in the
    # scrollback when the stream stops.
    def __init__(self, console):
        self.console = console
        self.acc = ""
        self.live = _RichLive("", refresh_per_second=12,
                              console=console)
        self.live.start()

    def feed(self, token):
        self.acc += token
        self.live.update(_RichMarkdown(self.acc))

    def finish(self):
        self.live.stop()


def _plain_terminal():
    # pipes, dumb terms, and --no-color/NO_COLOR all get the
    # plain renderer. rich only when a human is watching.
    if not _enabled:
        return True
    if os.environ.get("TERM") == "dumb":
        return True
    try:
        return not sys.stdout.isatty()
    except Exception:
        return True


def get_renderer():
    # rich when it is installed and a human is watching,
    # plain stdlib everywhere else. never raises.
    if _plain_terminal():
        return PlainRenderer()
    if _RICH:
        try:
            return RichRenderer()
        except Exception:
            pass
    return PlainRenderer()


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
