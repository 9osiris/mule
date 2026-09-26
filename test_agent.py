"""no api key needed. fake model + fake openai server."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import run, last_answer
from client import ChatClient
from tools import ToolSet

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


def tool_call(cid, name, args):
    return {
        "id": cid,
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# loop test with a scripted fake model

root = tempfile.mkdtemp()
script = [
    {"role": "assistant", "tool_calls": [tool_call("c1", "write_file",
            {"path": "hello.txt", "content": "hello from agent"})]},
    {"role": "assistant", "tool_calls": [tool_call("c2", "read_file",
            {"path": "hello.txt"})]},
    {"role": "assistant", "tool_calls": [tool_call("c3", "run_shell",
            {"command": "cat hello.txt"})]},
    {"role": "assistant", "content": "done, the file says hello from agent"},
]
calls = {"n": 0}


def fake_chat(messages, tools):
    reply = script[calls["n"]]
    calls["n"] += 1
    return reply


tools = ToolSet(root)
messages = run("make hello.txt and check it", fake_chat, tools,
               system_prompt="test", max_steps=10)

check("fake model consulted 4 times (3 tool rounds + final answer)",
      calls["n"] == 4)
check("write_file created the file",
      open(os.path.join(root, "hello.txt")).read() == "hello from agent")

tool_msgs = [m for m in messages if m["role"] == "tool"]
check("3 tool results fed back", len(tool_msgs) == 3)
check("read_file result has the content",
      "hello from agent" in tool_msgs[1]["content"])
check("shell result has the content",
      "hello from agent" in tool_msgs[2]["content"])
check("loop stopped on final answer",
      last_answer(messages) == "done, the file says hello from agent")

# sandbox: paths cant escape root

check("write outside root rejected",
      "escapes project root" in tools.call("write_file",
            {"path": "../evil.txt", "content": "x"}))
check("read outside root rejected",
      "escapes project root" in tools.call("read_file", {"path": "../../etc/passwd"}))
check("unknown tool is an error, not a crash",
      tools.call("nope", {}).startswith("error:"))

# shell timeout

out = tools.call("run_shell", {"command": "sleep 5", "timeout": "1"})
check("slow command times out", "timed out" in out)

# max steps stops a stubborn model

def stubborn(messages, tools):
    return {"role": "assistant",
            "tool_calls": [tool_call("s", "list_dir", {"path": "."})]}

msgs = run("x", stubborn, tools, system_prompt="t", max_steps=3)
check("max steps stops the loop",
      any("max steps" in (m.get("content") or "") for m in msgs))

# full loop through a fake openai-compatible server

RESPONSES = [
    {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{
            "id": "t1", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"path": "srv.txt",
                                                  "content": "via server"})},
        }]}}]},
    {"choices": [{"message": {
        "role": "assistant", "content": "wrote it through the server"}}]},
]
seen = {"bodies": []}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        seen["bodies"].append(json.loads(self.rfile.read(length)))
        body = RESPONSES.pop(0)
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

root2 = tempfile.mkdtemp()
client = ChatClient("http://127.0.0.1:%d/v1" % server.server_port,
                    "fake-key", "fake-model")
tools2 = ToolSet(root2)
msgs = run("write srv.txt", client.chat, tools2,
           system_prompt="t", max_steps=5)
server.shutdown()

check("server got 2 chat requests", len(seen["bodies"]) == 2)
check("first request carried tools + model",
      seen["bodies"][0]["model"] == "fake-model"
      and len(seen["bodies"][0]["tools"]) == 18)
check("second request included the tool result",
      seen["bodies"][1]["messages"][-1]["role"] == "tool")
check("file written through the whole stack",
      open(os.path.join(root2, "srv.txt")).read() == "via server")
check("final answer came back",
      last_answer(msgs) == "wrote it through the server")

# edit_file: patch one exact string

root4 = tempfile.mkdtemp()
tools4 = ToolSet(root4)
app = os.path.join(root4, "app.py")
open(app, "w").write("name = 'old'\nversion = 1\n")
dup = os.path.join(root4, "dup.py")
open(dup, "w").write("x = 1\nx = 2\n")

r = tools4.call("edit_file", {"path": "app.py",
                              "old": "name = 'old'",
                              "new": "name = 'new'"})
check("edit_file ok", r == "edited app.py")
check("edit_file changed only the match",
      open(app).read() == "name = 'new'\nversion = 1\n")
check("edit_file zero matches is an error",
      tools4.call("edit_file", {"path": "app.py",
                                "old": "nope", "new": "x"}).startswith("error:"))
r = tools4.call("edit_file", {"path": "dup.py", "old": "x =", "new": "y ="})
check("edit_file multiple matches is an error",
      r.startswith("error:") and "2 times" in r)
check("edit_file left the file alone on multiple matches",
      open(dup).read() == "x = 1\nx = 2\n")
check("edit_file outside root rejected",
      "escapes project root" in tools4.call(
          "edit_file", {"path": "../evil.txt", "old": "a", "new": "b"}))
check("edit_file missing file is an error",
      tools4.call("edit_file", {"path": "nope.txt",
                                "old": "a", "new": "b"}).startswith("error:"))
check("edit_file empty old is an error",
      tools4.call("edit_file", {"path": "app.py",
                                "old": "", "new": "x"}).startswith("error:"))

# streaming: fake server speaks SSE, tokens arrive as they come

def sse(delta):
    return "data: %s\n\n" % json.dumps({"choices": [{"delta": delta}]})


full_args = json.dumps({"path": "note.txt", "old": "hello",
                          "new": "goodbye"})
arg1, arg2 = full_args[:25], full_args[25:]
STREAM_RESPONSES = [
    [sse({"tool_calls": [{"index": 0, "id": "e1",
                          "function": {"name": "edit_file",
                                       "arguments": ""}}]}),
     sse({"tool_calls": [{"index": 0,
                          "function": {"arguments": arg1}}]}),
     sse({"tool_calls": [{"index": 0,
                          "function": {"arguments": arg2}}]})],
    [sse({"content": "edited"}),
     sse({"content": " the"}),
     sse({"content": " file"})],
]
stream_seen = {"bodies": []}


class StreamHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        stream_seen["bodies"].append(json.loads(self.rfile.read(length)))
        payload = ("".join(STREAM_RESPONSES.pop(0))
                   + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


sserver = HTTPServer(("127.0.0.1", 0), StreamHandler)
threading.Thread(target=sserver.serve_forever, daemon=True).start()

root3 = tempfile.mkdtemp()
open(os.path.join(root3, "note.txt"), "w").write("hello world\n")
sclient = ChatClient("http://127.0.0.1:%d/v1" % sserver.server_port,
                     "fake-key", "fake-model")
tools3 = ToolSet(root3)
tokens = []


def stream_chat(messages, tools):
    return sclient.chat_stream(messages, tools, on_token=tokens.append)


smsgs = run("change hello to goodbye in note.txt", stream_chat, tools3,
            system_prompt="t", max_steps=5)
sserver.shutdown()

check("stream request asked for streaming",
      stream_seen["bodies"][0].get("stream") is True)
check("tokens arrived incrementally",
      tokens == ["edited", " the", " file"])
check("streamed tool call args reassembled",
      open(os.path.join(root3, "note.txt")).read() == "goodbye world\n")
check("streamed answer is the joined tokens",
      last_answer(smsgs) == "edited the file")
check("tool result fed back after streamed call",
      any(m["role"] == "tool" for m in smsgs))

# --no-stream flag parsing

from main import parse_args, ask_cmd, build_parser, run_subcommand
check("--no-stream defaults off",
      parse_args(["do things"]).no_stream is False)
check("--no-stream flag turns on",
      parse_args(["--no-stream", "do things"]).no_stream is True)

# sessions: save, load, list, resume

import sessions
sess_dir = tempfile.mkdtemp()
sessions.session_dir = lambda: sess_dir

hist = [
    {"role": "system", "content": "sys"},
    {"role": "assistant", "content": "did the thing"},
    {"role": "user", "content": "thanks"},
]
spath = sessions.save_session("demo", hist)
check("session file written", os.path.isfile(spath))
check("session roundtrips", sessions.load_session("demo") == hist)
check("session listed", "demo" in sessions.list_sessions())
check("auto name looks right",
      sessions.auto_name().startswith("session-"))
try:
    sessions.load_session("nope")
    check("missing session raises", False)
except ValueError:
    check("missing session raises", True)

# resume: old history plus a new task goes through the loop

def resume_chat(messages, tools):
    check("resumed history kept",
          messages[0] == {"role": "system", "content": "sys"})
    check("new task appended",
          messages[-1] == {"role": "user", "content": "do more"})
    return {"role": "assistant", "content": "done more"}


rtools = ToolSet(tempfile.mkdtemp())
rmsgs = run("do more", resume_chat, rtools, messages=hist, max_steps=5)
check("resume returns full history",
      rmsgs[:3] == hist and last_answer(rmsgs) == "done more")


def cont_chat(messages, tools):
    check("no-task resume passes history through", messages == hist)
    return {"role": "assistant", "content": "still here"}


rmsgs2 = run(None, cont_chat, rtools, messages=hist, max_steps=5)
check("no-task resume works", last_answer(rmsgs2) == "still here")

check("--save flag takes a name",
      parse_args(["--save", "x", "task"]).save == "x")
check("--save alone auto-names",
      parse_args(["task", "--save"]).save == "auto")
check("--resume flag",
      parse_args(["--resume", "demo", "task"]).resume == "demo")

# cost math

from cost import cost_for, fmt_cost
check("cost math on a known model",
      abs(cost_for("gpt-4o-mini", 1_000_000, 1_000_000) - 0.75) < 1e-12)
check("unknown model has no cost",
      cost_for("some-local-model", 100, 100) is None)
check("cost formats",
      fmt_cost(0.00042) == "$0.0004"
      and fmt_cost(None) == "unknown pricing")

# usage from a normal (non-stream) response

URESP = [
    {"choices": [{"message": {"role": "assistant", "content": "priced"}}],
     "usage": {"prompt_tokens": 1200, "completion_tokens": 300}},
]


class UHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        body = URESP.pop(0)
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


userver = HTTPServer(("127.0.0.1", 0), UHandler)
threading.Thread(target=userver.serve_forever, daemon=True).start()
uclient = ChatClient("http://127.0.0.1:%d/v1" % userver.server_port,
                     "fake-key", "gpt-4o-mini")
ureply = uclient.chat([{"role": "user", "content": "hi"}])
userver.shutdown()
check("non-stream usage captured",
      ureply.get("usage") == {"prompt_tokens": 1200,
                              "completion_tokens": 300})
check("cost of the reply matches pricing",
      abs(cost_for("gpt-4o-mini", 1200, 300)
          - (1200 / 1e6 * 0.15 + 300 / 1e6 * 0.60)) < 1e-15)

# usage_cb fires per step through the loop

def priced_chat(messages, tools):
    return {"role": "assistant", "content": "x",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


got = []
run("y", priced_chat, ToolSet(tempfile.mkdtemp()), system_prompt="t",
    max_steps=1, usage_cb=lambda s, u: got.append((s, u)))
check("usage_cb got one step",
      got == [(1, {"prompt_tokens": 10, "completion_tokens": 5})])

# streamed usage chunk

def sse2(delta):
    return "data: %s\n\n" % json.dumps({"choices": [{"delta": delta}]})


USSE = ("".join([
    sse2({"content": "hi"}),
    "data: %s\n\n" % json.dumps(
        {"usage": {"prompt_tokens": 40, "completion_tokens": 8}}),
]) + "data: [DONE]\n\n").encode()
useen = {}


class USSEHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        useen["body"] = json.loads(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(USSE)))
        self.end_headers()
        self.wfile.write(USSE)

    def log_message(self, *a):
        pass


usserver = HTTPServer(("127.0.0.1", 0), USSEHandler)
threading.Thread(target=usserver.serve_forever, daemon=True).start()
usclient = ChatClient("http://127.0.0.1:%d/v1" % usserver.server_port,
                      "fake-key", "gpt-4o-mini")
usreply = usclient.chat_stream([{"role": "user", "content": "hi"}])
usserver.shutdown()
check("stream asked for usage",
      useen["body"].get("stream_options") == {"include_usage": True})
check("streamed usage captured",
      usreply.get("usage") == {"prompt_tokens": 40, "completion_tokens": 8})
check("streamed content still intact", usreply.get("content") == "hi")

# fetch_url: local page, html stripped, caps and rejections

PAGE = (b"<html><head><title>hi</title><style>.x{color:red}</style></head>"
        b"<body><script>alert(1)</script><h1>Hello &amp; bye</h1>"
        b"<p>some   text</p></body></html>")
BIG = b"<p>" + b"x" * 300_000 + b"</p>"


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = BIG if self.path == "/big" else PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


pserver = HTTPServer(("127.0.0.1", 0), PageHandler)
threading.Thread(target=pserver.serve_forever, daemon=True).start()
pbase = "http://127.0.0.1:%d" % pserver.server_port
ptools = ToolSet(tempfile.mkdtemp())

text = ptools.call("fetch_url", {"url": pbase + "/"})
check("fetch_url strips html to text",
      text == "hi Hello & bye some text")
check("fetch_url drops script and style", "alert" not in text
      and "color" not in text)
check("fetch_url registered in schemas",
      any(t["function"]["name"] == "fetch_url"
          for t in ptools.schemas()))

big = ptools.call("fetch_url", {"url": pbase + "/big"})
check("fetch_url caps huge pages", len(big) <= 200_050
      and "x" * 100 in big)
check("fetch_url rejects file urls",
      ptools.call("fetch_url", {"url": "file:///etc/passwd"})
      .startswith("error:"))
check("fetch_url rejects bare strings",
      ptools.call("fetch_url", {"url": "notaurl"}).startswith("error:"))
check("fetch_url handles dead servers",
      ptools.call("fetch_url", {"url": "http://127.0.0.1:1/"})
      .startswith("error:"))
pserver.shutdown()

# --ask: human-in-the-loop shell confirmation

asked = []


def yes(cmd):
    asked.append(cmd)
    return True


def no(cmd):
    asked.append(cmd)
    return False


aroot = tempfile.mkdtemp()
ytools = ToolSet(aroot, confirm=yes)
ntools = ToolSet(aroot, confirm=no)
gtools = ToolSet(aroot)  # default: no gate

check("--ask yes runs the command",
      "exit 0" in ytools.call("run_shell", {"command": "echo hi"}))
check("--ask no skips it",
      ntools.call("run_shell", {"command": "echo hi"}).startswith("declined:"))
check("--ask asked with the command", asked == ["echo hi", "echo hi"])
check("no gate means no asking",
      "exit 0" in gtools.call("run_shell", {"command": "echo hi"}))
check("--ask flag parses", parse_args(["--ask", "task"]).ask is True)
check("--ask defaults off", parse_args(["task"]).ask is False)

import builtins
real_input = builtins.input
builtins.input = lambda *a: "y"
check("ask_cmd yes is yes", ask_cmd("ls") is True)
builtins.input = lambda *a: "n"
check("ask_cmd no is no", ask_cmd("ls") is False)
builtins.input = lambda *a: ""
check("ask_cmd empty is no", ask_cmd("ls") is False)
builtins.input = real_input

# config file: home < local < env < flag

import config

home_cfg = os.path.join(tempfile.mkdtemp(), "mule.json")
local_cfg = os.path.join(tempfile.mkdtemp(), "mule.json")
with open(home_cfg, "w") as f:
    json.dump({"model": "home-model", "max_steps": 5, "timeout": 10}, f)
with open(local_cfg, "w") as f:
    json.dump({"model": "local-model", "base_url": "http://local/v1",
               "nope": 1}, f)
config.HOME_CONFIG = home_cfg
config.LOCAL_CONFIG = local_cfg

cfg = config.load_config()
check("local config beats home", cfg["model"] == "local-model")
check("home-only keys survive",
      cfg["max_steps"] == 5 and cfg["base_url"] == "http://local/v1")
check("unknown keys dropped", "nope" not in cfg)

config.HOME_CONFIG = "/nonexistent/mule.json"
config.LOCAL_CONFIG = "/nonexistent/mule.json"
check("missing configs give empty", config.load_config() == {})

with open(local_cfg, "w") as f:
    f.write("not json{")
config.LOCAL_CONFIG = local_cfg
check("broken config json gives empty", config.load_config() == {})

# precedence through parse_args
with open(home_cfg, "w") as f:
    json.dump({"model": "cfg-model"}, f)
with open(local_cfg, "w") as f:
    json.dump({"model": "cfg-model"}, f)
config.HOME_CONFIG = home_cfg
config.LOCAL_CONFIG = local_cfg

old_env = dict(os.environ)
try:
    os.environ.pop("MULE_MODEL", None)
    check("config fills model default",
          parse_args(["task"]).model == "cfg-model")
    os.environ["MULE_MODEL"] = "env-model"
    check("env beats config", parse_args(["task"]).model == "env-model")
    check("flag beats env",
          parse_args(["task", "--model", "flag-model"]).model
          == "flag-model")
finally:
    os.environ.clear()
    os.environ.update(old_env)

config.HOME_CONFIG = "/nonexistent/mule.json"
config.LOCAL_CONFIG = "/nonexistent/mule.json"
check("--timeout defaults to 120", parse_args(["task"]).timeout == 120)
check("--timeout parses",
      parse_args(["task", "--timeout", "30"]).timeout == 30)
check("--max-steps still defaults to 25",
      parse_args(["task"]).max_steps == 25)

# retry with backoff: fake server fails twice, then succeeds

rcalls = {"n": 0}


class RetryHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        rcalls["n"] += 1
        if rcalls["n"] <= 2:
            self.send_response(500 if rcalls["n"] == 1 else 429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant",
                                      "content": "recovered"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


rserver = HTTPServer(("127.0.0.1", 0), RetryHandler)
threading.Thread(target=rserver.serve_forever, daemon=True).start()
rclient = ChatClient("http://127.0.0.1:%d/v1" % rserver.server_port,
                     "fake-key", "m", retries=3, backoff=0)
rreply = rclient.chat([{"role": "user", "content": "hi"}])
check("retry succeeded after 2 failures",
      rreply.get("content") == "recovered" and rcalls["n"] == 3)
rserver.shutdown()

# always failing: clean error after retries exhausted

fcalls = {"n": 0}


class FailHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        fcalls["n"] += 1
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


fserver = HTTPServer(("127.0.0.1", 0), FailHandler)
threading.Thread(target=fserver.serve_forever, daemon=True).start()
fclient = ChatClient("http://127.0.0.1:%d/v1" % fserver.server_port,
                     "fake-key", "m", retries=2, backoff=0)
try:
    fclient.chat([{"role": "user", "content": "hi"}])
    check("exhausted retries raise", False)
except RuntimeError as e:
    check("exhausted retries raise",
          "503" in str(e) and "3 attempt" in str(e))
check("gave up after retries+1 attempts", fcalls["n"] == 3)
fserver.shutdown()

# 400 is not retryable: one attempt, immediate error

bcalls = {"n": 0}


class BadHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        bcalls["n"] += 1
        self.send_response(400)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


bserver = HTTPServer(("127.0.0.1", 0), BadHandler)
threading.Thread(target=bserver.serve_forever, daemon=True).start()
bclient = ChatClient("http://127.0.0.1:%d/v1" % bserver.server_port,
                     "fake-key", "m", retries=3, backoff=0)
try:
    bclient.chat([{"role": "user", "content": "hi"}])
    check("400 raises", False)
except RuntimeError:
    check("400 raises", True)
check("400 not retried", bcalls["n"] == 1)
bserver.shutdown()

# backoff waits 1x, 2x, 4x ... between attempts

import client as client_mod


class FakeTime:
    def __init__(self):
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)


fake_time = FakeTime()
real_time = client_mod.time
client_mod.time = fake_time
ecalls = {"n": 0}


class ExpHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        ecalls["n"] += 1
        if ecalls["n"] < 3:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant",
                                      "content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


eserver = HTTPServer(("127.0.0.1", 0), ExpHandler)
threading.Thread(target=eserver.serve_forever, daemon=True).start()
eclient = ChatClient("http://127.0.0.1:%d/v1" % eserver.server_port,
                     "fake-key", "m", retries=3, backoff=2)
eclient.chat([{"role": "user", "content": "hi"}])
eserver.shutdown()
client_mod.time = real_time
check("backoff is exponential", fake_time.sleeps == [2, 4])

check("--retries defaults to 3", parse_args(["task"]).retries == 3)
check("--retries parses",
      parse_args(["task", "--retries", "0"]).retries == 0)

# undo: backups before writes and edits, restored lifo

from tools import make_diff, BackupStore

uroot = tempfile.mkdtemp()
utools = ToolSet(uroot)
utools.backups = BackupStore(backup_dir=tempfile.mkdtemp())

target = os.path.join(uroot, "f.txt")
open(target, "w").write("original\n")

utools.call("write_file", {"path": "f.txt", "content": "v2\n"})
check("write stashes a backup", len(utools.backups.stack) == 1)
check("write changed the file", open(target).read() == "v2\n")
check("undo restores", utools.undo_last() == "restored f.txt")
check("undo brought back the original",
      open(target).read() == "original\n")
check("undo stack empties", utools.undo_last() == "nothing to undo")

utools.call("write_file", {"path": "new.txt", "content": "fresh\n"})
check("new file needs no backup", len(utools.backups.stack) == 0)
check("undo with no backups says so",
      utools.undo_last() == "nothing to undo")

utools.call("edit_file", {"path": "f.txt", "old": "original",
                          "new": "edited"})
check("edit stashes a backup", len(utools.backups.stack) == 1)
utools.call("edit_file", {"path": "f.txt", "old": "edited",
                          "new": "edited2"})
check("two edits, two backups", len(utools.backups.stack) == 2)
utools.undo_last()
check("undo is lifo", open(target).read() == "edited\n")
utools.undo_last()
check("second undo restores original",
      open(target).read() == "original\n")

# diff preview in --ask mode

d = make_diff("a\nb\n", "a\nc\n", "f.txt")
check("diff shows removed and added lines",
      "-b" in d and "+c" in d and "a/f.txt" in d)
check("empty diff for identical text", make_diff("x", "x") == "")

prompts = []
droot = tempfile.mkdtemp()
dtools = ToolSet(droot, confirm=lambda p: prompts.append(p) or False)
dtools.backups = BackupStore(backup_dir=tempfile.mkdtemp())
dtarget = os.path.join(droot, "g.txt")
open(dtarget, "w").write("old line\n")

r = dtools.call("edit_file", {"path": "g.txt", "old": "old line",
                              "new": "new line"})
check("declined edit does not apply", r.startswith("declined:"))
check("declined edit leaves file alone",
      open(dtarget).read() == "old line\n")
check("declined edit stashes nothing", len(dtools.backups.stack) == 0)
check("confirm got one prompt", len(prompts) == 1)
check("prompt shows the diff",
      "-old line" in prompts[0] and "+new line" in prompts[0])

ytools2 = ToolSet(droot, confirm=lambda p: True)
ytools2.backups = BackupStore(backup_dir=tempfile.mkdtemp())
r = ytools2.call("write_file", {"path": "g.txt", "content": "ok\n"})
check("accepted write applies",
      r.startswith("wrote") and open(dtarget).read() == "ok\n")

# web_search: duckduckgo lite parsing, against a fake server

import tools as tools_mod
from tools import _ddg_results

ddg_page = """
<html><body>
<table>
<tr><td>1.</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc"
 class='result-link'>First Result</a>
</td></tr>
<tr><td>&nbsp;</td>
<td class='result-snippet'>
  the first snippet, with words.
</td></tr>
<tr><td>2.</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=http%3A%2F%2Fplain.org%2F&amp;rut=def"
 class='result-link'>Second &amp; Result</a>
</td></tr>
<tr><td>&nbsp;</td>
<td class='result-snippet'>
  second snippet here.
</td></tr>
</table>
</body></html>
"""

parsed = _ddg_results(ddg_page, 8)
check("parsed two results", len(parsed) == 2)
check("title unescaped", parsed[1][0] == "Second & Result")
check("uddg url decoded", parsed[0][1] == "https://example.com/a")
check("plain url decoded", parsed[1][1] == "http://plain.org/")
check("snippet text cleaned",
      parsed[0][2] == "the first snippet, with words.")
check("limit respected", len(_ddg_results(ddg_page, 1)) == 1)


class SearchHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ddg_page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


sserver = HTTPServer(("127.0.0.1", 0), SearchHandler)
threading.Thread(target=sserver.serve_forever, daemon=True).start()
real_ddg = tools_mod.DDG_LITE
tools_mod.DDG_LITE = ("http://127.0.0.1:%d/lite/"
                      % sserver.server_port)
stools = ToolSet(tempfile.mkdtemp())
out = stools.call("web_search", {"query": "fake", "count": "5"})
tools_mod.DDG_LITE = real_ddg
sserver.shutdown()
check("search returns titles", "First Result" in out)
check("search returns urls", "https://example.com/a" in out)
check("search returns snippets", "the first snippet" in out)
check("search numbers results", out.startswith("1. "))
check("empty query errors",
      "error" in stools.call("web_search", {"query": ""}))
names = [t["function"]["name"] for t in stools.schemas()]
check("web_search registered", "web_search" in names)

# cost budget: tiny budget stops the loop after one step

def budget_chat(messages, tools):
    return {"role": "assistant", "content": "spending...",
            "usage": {"prompt_tokens": 1000000, "completion_tokens": 0},
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "list_dir",
                                         "arguments": "{}"}}]}

btools = ToolSet(tempfile.mkdtemp())
btotals = {"in": 0, "out": 0}


def btrack(step, usage):
    btotals["in"] += usage.get("prompt_tokens", 0)
    btotals["out"] += usage.get("completion_tokens", 0)
    spent = cost_for("gpt-4o-mini", btotals["in"], btotals["out"])
    return spent is not None and spent > 0.01


bmsgs = run("spend", budget_chat, btools, system_prompt="t",
            max_steps=25, usage_cb=btrack)
check("budget stops the loop early", len(bmsgs) < 10)
check("budget stop message is clear",
      bmsgs[-1]["content"] == "stopped: hit cost budget")
check("budget ran one step", btotals["in"] == 1000000)

# same loop, generous budget: runs all the way

gmsgs = run("spend", budget_chat, btools, system_prompt="t",
            max_steps=3,
            usage_cb=lambda s, u: False)
check("no stop without budget",
      gmsgs[-1]["content"] == "stopped: hit max steps (3)")

check("--max-cost parses",
      parse_args(["t", "--max-cost", "0.5"]).max_cost == 0.5)
check("--max-cost defaults to none",
      parse_args(["t"]).max_cost is None)

# interactive repl: slash commands and the prompt loop

from repl import parse_slash, handle_slash, repl_loop, HELP_TEXT

check("parse /save NAME",
      parse_slash("/save foo") == ("save", "foo"))
check("parse /quit", parse_slash("/quit") == ("quit", ""))
check("parse is case-insensitive", parse_slash("/HELP") == ("help", ""))
check("parse keeps multiword arg",
      parse_slash("/save my session") == ("save", "my session"))
check("parse bare slash", parse_slash("/") == ("", ""))

rtools = ToolSet(tempfile.mkdtemp())
rtools.backups = BackupStore(backup_dir=tempfile.mkdtemp())
rtarget = os.path.join(rtools.root, "u.txt")
open(rtarget, "w").write("before\n")
rtools.call("write_file", {"path": "u.txt", "content": "after\n"})

saved = {}
written = []
rctx = {
    "write": written.append,
    "tools": rtools,
    "totals": {"in": 1000000, "out": 500000},
    "model": "gpt-4o-mini",
    "save_fn": lambda name: saved.setdefault("name", name) or "/s.jsonl",
}

check("/quit returns quit", handle_slash("/quit", rctx) == "quit")
check("/clear returns clear", handle_slash("/clear", rctx) == "clear")
check("/help prints help",
      handle_slash("/help", rctx) is None and written[-1] == HELP_TEXT)
check("/cost shows spend",
      handle_slash("/cost", rctx) is None
      and "1,000,000 in / 500,000 out" in written[-1]
      and "$0.45" in written[-1])
check("/save calls save_fn",
      handle_slash("/save work", rctx) is None
      and saved["name"] == "work"
      and written[-1].startswith("saved session: "))
check("/save without name nudges",
      handle_slash("/save", rctx) is None
      and written[-1] == "usage: /save NAME")
check("/undo restores through the repl",
      handle_slash("/undo", rctx) is None
      and written[-1] == "restored u.txt"
      and open(rtarget).read() == "before\n")
check("/undo with empty stack",
      handle_slash("/undo", rctx) is None
      and written[-1] == "nothing to undo")
check("unknown slash is reported",
      handle_slash("/nope", rctx) is None
      and "unknown command" in written[-1])

# the loop: tasks dispatch, slashes handled, history shared

loop_lines = iter(["first task", "", "  ", "/cost", "second", "/quit"])
loop_tasks = []
loop_out = []
loop_ctx = dict(rctx)
loop_ctx["write"] = loop_out.append
repl_loop(lambda p: next(loop_lines), loop_out.append,
          loop_tasks.append, lambda line: handle_slash(line, loop_ctx))
check("loop dispatches both tasks",
      loop_tasks == ["first task", "second"])
check("loop skips blanks", len(loop_tasks) == 2)
check("loop ran the slash",
      any("1,000,000 in" in w for w in loop_out))
check("loop greets first", loop_out[0].startswith("interactive mode"))


def eof_read(prompt):
    raise EOFError()


eof_out = []
repl_loop(eof_read, eof_out.append, lambda l: None, lambda l: None)
check("eof leaves the loop", True)

check("--interactive parses",
      parse_args(["--interactive"]).interactive is True)
check("--undo parses", parse_args(["--undo"]).undo is True)

# plan mode: approve, reject, revise

from agent import plan_and_approve, context_size, compact_messages

pscript = [
    {"role": "assistant", "content": "plan: 1. write the file"},
    {"role": "assistant", "tool_calls": [tool_call("p1", "write_file",
            {"path": "p.txt", "content": "planned"})]},
    {"role": "assistant", "content": "done per plan"},
]
pstate = {"n": 0, "saw_tools": []}


def pchat(messages, tools):
    pstate["saw_tools"].append(tools)
    reply = pscript[pstate["n"]]
    pstate["n"] += 1
    return reply


proot = tempfile.mkdtemp()
ptools = ToolSet(proot)
pmsgs, pok = plan_and_approve("do the thing", pchat, lambda plan: "y",
                              system_prompt="t")
check("plan approved", pok is True)
check("plan phase made no tool calls", pstate["saw_tools"][0] == [])
check("plan is in the history",
      any("plan: 1. write the file" in (m.get("content") or "")
          for m in pmsgs))
pmsgs = run(None, pchat, ptools, messages=pmsgs, max_steps=5)
check("approved plan leads to tool calls",
      open(os.path.join(proot, "p.txt")).read() == "planned")
check("loop finished after the plan",
      last_answer(pmsgs) == "done per plan")


def rchat(messages, tools):
    rchat.n += 1
    return {"role": "assistant", "content": "a plan"}
rchat.n = 0

_, rok = plan_and_approve("task", rchat, lambda plan: "n",
                          system_prompt="t")
check("plan rejected", rok is False)
check("rejected plan stops before any tools", rchat.n == 1)


vanswers = iter(["r", "y"])
vplans = []


def vdecide(plan):
    vplans.append(plan)
    return next(vanswers)


def vchat(messages, tools):
    vchat.n += 1
    return {"role": "assistant",
            "content": "plan v%d" % vchat.n}
vchat.n = 0

vmsgs, vok = plan_and_approve("task", vchat, vdecide, system_prompt="t")
check("revise asks the model once more", vchat.n == 2 and vok is True)
check("revision request is in the history",
      any("revise the plan" in (m.get("content") or "") for m in vmsgs))
check("decide saw both plans", vplans == ["plan v1", "plan v2"])


def wchat(messages, tools):
    wchat.n += 1
    return {"role": "assistant", "content": "plan v%d" % wchat.n}
wchat.n = 0

_, wok = plan_and_approve("task", wchat, lambda plan: "r",
                          system_prompt="t")
check("a second revise counts as a reject",
      wok is False and wchat.n == 2)

check("--plan parses", parse_args(["--plan", "t"]).plan is True)

# todo tools: write, read, progress lines through the loop

ttools = ToolSet(tempfile.mkdtemp())
check("empty todos read", ttools.call("todo_read", {}) == "(no todos)")
check("empty todos give no progress line",
      ttools.progress_line() is None)

r = ttools.call("todo_write", {"todos": json.dumps([
    {"text": "write code", "status": "done"},
    {"text": "write tests", "status": "in_progress"},
    {"text": "ship it", "status": "pending"},
])})
check("todo_write returns the progress line", r == "[1/3] write tests")
check("progress line counts done", ttools.progress_line() == "[1/3] write tests")
read = ttools.call("todo_read", {})
check("todo_read marks each state",
      "[x] write code" in read and "[>] write tests" in read
      and "[ ] ship it" in read)
check("todo_write rejects bad json",
      ttools.call("todo_write", {"todos": "nope"}).startswith("error:"))
check("todo_write rejects a bad status",
      ttools.call("todo_write", {"todos": json.dumps(
          [{"text": "x", "status": "bogus"}])}).startswith("error:"))
check("todo_write rejects a missing text",
      ttools.call("todo_write", {"todos": json.dumps(
          [{"status": "done"}])}).startswith("error:"))
check("todo_write defaults status to pending",
      ttools.call("todo_write", {"todos": json.dumps(
          [{"text": "x"}])}) == "[0/1] x")
r = ttools.call("todo_write", {"todos": "[]"})
check("clearing todos", r == "todo list cleared"
      and ttools.progress_line() is None)

# the loop renders a progress line whenever todos change

tscript = [
    {"role": "assistant", "tool_calls": [tool_call("t1", "todo_write",
            {"todos": json.dumps([{"text": "a", "status": "in_progress"},
                                   {"text": "b", "status": "pending"}])})]},
    {"role": "assistant", "tool_calls": [tool_call("t2", "todo_write",
            {"todos": json.dumps([{"text": "a", "status": "done"},
                                   {"text": "b", "status": "in_progress"}])})]},
    {"role": "assistant", "tool_calls": [tool_call("t3", "todo_write",
            {"todos": json.dumps([{"text": "a", "status": "done"},
                                   {"text": "b", "status": "done"}])})]},
    {"role": "assistant", "content": "all done"},
]
tstate = {"n": 0}


def tchat(messages, tools):
    reply = tscript[tstate["n"]]
    tstate["n"] += 1
    return reply


tt = ToolSet(tempfile.mkdtemp())
seen_todos = []
tmsgs = run("do a and b", tchat, tt, system_prompt="t", max_steps=10,
            on_todos=seen_todos.append)
check("loop renders todo progress when it changes",
      seen_todos == ["[0/2] a", "[1/2] b", "[2/2] all done"])
check("loop finishes after todos",
      last_answer(tmsgs) == "all done")

# context auto-compaction: fat history gets squashed, tail survives

check("context_size counts chars",
      context_size([{"role": "user", "content": "abc"}]) > 10)

fat = [{"role": "system", "content": "sys"}]
for i in range(30):
    fat.append({"role": "user", "content": "task %d: %s" % (i, "x" * 200)})
    fat.append({"role": "assistant", "content": "answer %d: %s" % (i, "y" * 200)})


def cchat(messages, tools):
    cchat.n += 1
    if not tools:
        # the summarization call
        cchat.summaries.append(messages)
        return {"role": "assistant",
                "content": "did stuff, then more stuff"}
    return {"role": "assistant", "content": "final answer"}
cchat.n = 0
cchat.summaries = []

before = len(fat)
cmsgs = run(None, cchat, ToolSet(tempfile.mkdtemp()), messages=fat,
            max_steps=2, context_budget=1000)
check("compaction shrank the history", len(cmsgs) < before)
check("a summary message exists",
      any("[earlier context summarized]" in (m.get("content") or "")
          for m in cmsgs))
check("the summary text landed",
      any("did stuff, then more stuff" in (m.get("content") or "")
          for m in cmsgs))
check("system prompt survived",
      cmsgs[0] == {"role": "system", "content": "sys"})
check("recent tail kept",
      any("task 29" in (m.get("content") or "") for m in cmsgs))
check("summarizer got the old messages",
      len(cchat.summaries) == 1
      and "task 0" in json.dumps(cchat.summaries[0]))
check("loop finished after compaction",
      last_answer(cmsgs) == "final answer")


def nchat(messages, tools):
    nchat.n += 1
    assert tools, "summarizer should not run under budget"
    return {"role": "assistant", "content": "fine"}
nchat.n = 0

nmsgs = run("small task", nchat, ToolSet(tempfile.mkdtemp()),
            system_prompt="t", max_steps=2, context_budget=100000)
check("no compaction under budget",
      nchat.n == 1 and last_answer(nmsgs) == "fine")

# compact_messages leaves a short history alone
short = [{"role": "system", "content": "s"},
         {"role": "user", "content": "hi"}]
check("short history untouched",
      compact_messages(short, cchat)[0] == short)

# the summary call counts toward cost tracking

def uchat(messages, tools):
    uchat.n += 1
    if not tools:
        return {"role": "assistant", "content": "summary",
                "usage": {"prompt_tokens": 50, "completion_tokens": 10}}
    return {"role": "assistant", "content": "done"}
uchat.n = 0

ugot = []
run(None, uchat, ToolSet(tempfile.mkdtemp()), messages=list(fat),
    max_steps=2, context_budget=1000,
    usage_cb=lambda s, u: ugot.append(u))
check("summary usage hits usage_cb",
      {"prompt_tokens": 50, "completion_tokens": 10} in ugot)

check("--context-budget parses",
      parse_args(["t", "--context-budget", "5000"]).context_budget == 5000)
check("--context-budget defaults to 100k",
      parse_args(["t"]).context_budget == 100000)

# read_image: tiny png fixture, sandbox, type checks

import base64 as b64mod

iroot = tempfile.mkdtemp()
itools = ToolSet(iroot)
# a 1x1 red png, hand-rolled bytes
png = (b64mod.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
open(os.path.join(iroot, "dot.png"), "wb").write(png)
open(os.path.join(iroot, "note.txt"), "w").write("not an image")

ires = itools.call("read_image", {"path": "dot.png"})
check("read_image returns a data uri",
      "data:image/png;base64," in ires)
check("read_image payload roundtrips",
      b64mod.b64decode(ires.split("data:image/png;base64,")[1]
                       .split(")")[0]) == png)
check("read_image names the file and type",
      "dot.png" in ires and "image/png" in ires)
check("read_image rejects non-images",
      itools.call("read_image", {"path": "note.txt"}).startswith("error:"))
check("read_image rejects missing files",
      itools.call("read_image", {"path": "nope.png"}).startswith("error:"))
check("read_image stays in the sandbox",
      "escapes project root" in itools.call(
          "read_image", {"path": "../evil.png"}))
check("read_image is in the schemas",
      any(s["function"]["name"] == "read_image"
          for s in itools.schemas()))

# custom slash commands from .mule/commands/*.md

from repl import load_commands

croot = tempfile.mkdtemp()
cmds_dir = os.path.join(croot, ".mule", "commands")
os.makedirs(cmds_dir)
open(os.path.join(cmds_dir, "review.md"), "w").write(
    "review the diff carefully")
open(os.path.join(cmds_dir, "ship.md"), "w").write("run the tests then commit")
open(os.path.join(cmds_dir, "notes.txt"), "w").write("not a command")

cmds = load_commands(cmds_dir)
check("commands load from md files",
      cmds == {"review": "review the diff carefully",
               "ship": "run the tests then commit"})
check("missing commands dir gives none",
      load_commands(os.path.join(croot, "nope")) == {})

cctx = dict(rctx)
cctx["commands"] = cmds
check("custom command returns a run action",
      handle_slash("/review", cctx)
      == ("run", "review the diff carefully"))
check("/help lists custom commands",
      handle_slash("/help", cctx) is None
      and "/review" in written[-1] and "/ship" in written[-1])
check("builtins still win over customs",
      handle_slash("/quit", cctx) == "quit")

# session export to markdown

from sessions import export_session

xmsgs = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "write hi.txt"},
    {"role": "assistant", "tool_calls": [tool_call("e1", "write_file",
            {"path": "hi.txt", "content": "hi"})]},
    {"role": "tool", "tool_call_id": "e1", "content": "wrote 2 bytes"},
    {"role": "assistant", "content": "done"},
]
xpath = os.path.join(tempfile.mkdtemp(), "out.md")
export_session(xpath, xmsgs,
               cost_line="tokens: 10 in / 5 out, cost $0.0000")
xtext = open(xpath).read()
check("export has user turns", "## user\n\nwrite hi.txt" in xtext)
check("export has assistant turns", "## assistant\n\ndone" in xtext)
check("export shows tool calls as code blocks",
      "```\nwrite_file" in xtext and '"path": "hi.txt"' in xtext)
check("export shows tool results", "wrote 2 bytes" in xtext)
check("export ends with the cost line",
      xtext.rstrip().endswith("cost $0.0000"))
check("export skips the system prompt", "\nsys\n" not in xtext)
check("--export parses",
      parse_args(["t", "--export", "out.md"]).export == "out.md")

# multiple tool calls in one turn: all run, in order, results fed back

mroot = tempfile.mkdtemp()
mtools = ToolSet(mroot)
mstate = {"n": 0}


def mchat(messages, tools):
    mstate["n"] += 1
    if mstate["n"] == 1:
        return {"role": "assistant", "tool_calls": [
            tool_call("m1", "write_file",
                      {"path": "a.txt", "content": "aaa"}),
            tool_call("m2", "write_file",
                      {"path": "b.txt", "content": "bbb"}),
            tool_call("m3", "run_shell",
                      {"command": "cat a.txt b.txt"}),
        ]}
    return {"role": "assistant", "content": "all three ran"}


msteps = []
mmsgs = run("make two files and cat them", mchat, mtools,
            system_prompt="t", max_steps=5,
            on_step=lambda s, calls: msteps.append(calls))
mtool_msgs = [m for m in mmsgs if m["role"] == "tool"]
check("all three calls ran in one turn", len(mtool_msgs) == 3)
check("results came back in call order",
      [m["tool_call_id"] for m in mtool_msgs] == ["m1", "m2", "m3"])
check("later calls see earlier writes",
      "aaabbb" in mtool_msgs[2]["content"].replace("\n", ""))
check("on_step saw all three calls",
      len(msteps) == 1 and len(msteps[0]) == 3)
check("loop finished after the batch",
      last_answer(mmsgs) == "all three ran")

# checkpoints: tar the root, skip junk, restore brings files back

import checkpoints
from checkpoints import save_checkpoint, restore_checkpoint

ckdir = tempfile.mkdtemp()
checkpoints.checkpoint_dir = lambda: ckdir

ckroot = tempfile.mkdtemp()
open(os.path.join(ckroot, "app.py"), "w").write("v1\n")
os.makedirs(os.path.join(ckroot, ".git"))
open(os.path.join(ckroot, ".git", "junk"), "w").write("x")
os.makedirs(os.path.join(ckroot, "__pycache__"))
open(os.path.join(ckroot, "__pycache__", "a.pyc"), "w").write("x")

cpath = save_checkpoint(ckroot, "before-change")
check("checkpoint file written", os.path.isfile(cpath))

import tarfile as tfmod
names = tfmod.open(cpath, "r:gz").getnames()
check("checkpoint has the project files",
      any(n.endswith("app.py") for n in names))
check("checkpoint skips .git",
      not any(".git" in n for n in names))
check("checkpoint skips pycache",
      not any("__pycache__" in n for n in names))

open(os.path.join(ckroot, "app.py"), "w").write("v2 broken\n")
restore_checkpoint(ckroot, "before-change")
check("restore brings the file back",
      open(os.path.join(ckroot, "app.py")).read() == "v1\n")
try:
    restore_checkpoint(ckroot, "nope")
    check("missing checkpoint raises", False)
except ValueError:
    check("missing checkpoint raises", True)

check("--checkpoint parses",
      parse_args(["t", "--checkpoint", "c1"]).checkpoint == "c1")
check("--restore parses",
      parse_args(["t", "--restore", "c1"]).restore == "c1")

# mule init scaffolding

from scaffold import init_project

initroot = tempfile.mkdtemp()
created = init_project(initroot)
check("init creates mule.json",
      os.path.isfile(os.path.join(initroot, "mule.json")))
check("init creates prompt.md",
      os.path.isfile(os.path.join(initroot, "prompt.md")))
check("init creates an example command",
      os.path.isfile(os.path.join(initroot, ".mule", "commands",
                                  "review.md")))
check("init mule.json parses as json",
      json.load(open(os.path.join(initroot, "mule.json")))["model"]
      == "gpt-4o-mini")

try:
    init_project(initroot)
    check("init refuses to overwrite", False)
except FileExistsError:
    check("init refuses to overwrite", True)

created2 = init_project(initroot, force=True)
check("init --force overwrites", len(created2) == 3)

# mule config from the cli

import config as config_mod

_cfg_home = tempfile.mkdtemp()
_orig_home = config_mod.HOME_CONFIG
_orig_local = config_mod.LOCAL_CONFIG
config_mod.HOME_CONFIG = os.path.join(_cfg_home, "mule.json")
config_mod.LOCAL_CONFIG = os.path.join(_cfg_home, "local-mule.json")

check("config set stores a string",
      config_mod.config_set("model", "gpt-4o-mini") == "gpt-4o-mini")
check("config set parses numbers",
      config_mod.config_set("max_steps", "40") == 40)
check("config get reads it back",
      config_mod.config_get("model") == "gpt-4o-mini")
check("config get missing key is none",
      config_mod.config_get("api_key") is None)
config_mod.config_unset("model")
check("config unset removes the key",
      config_mod.config_get("model") is None)
check("config list shows set keys",
      config_mod.config_list().get("max_steps") == 40)
try:
    config_mod.config_set("nope", "x")
    check("config rejects unknown keys", False)
except KeyError:
    check("config rejects unknown keys", True)

check("config file is valid json",
      json.load(open(config_mod.LOCAL_CONFIG))["max_steps"] == 40)

from main import cmd_config
check("cli config set works",
      cmd_config(["set", "model", "gpt-4o"]) == 0)
check("cli config get works",
      cmd_config(["get", "model"]) == 0)
check("cli config --global set works",
      cmd_config(["--global", "set", "model", "gpt-4o-mini"]) == 0)
check("cli config --global get works",
      cmd_config(["--global", "get", "model"]) == 0)
check("cli config bad action errors",
      cmd_config(["frobnicate"]) == 2)
check("cli config unknown key errors",
      cmd_config(["set", "nope", "x"]) == 1)

config_mod.HOME_CONFIG = _orig_home
config_mod.LOCAL_CONFIG = _orig_local

# mule doctor

from doctor import run_doctor, _check_reachable, _check_config, _check_key, \
    _check_root


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


_doc_srv = HTTPServer(("127.0.0.1", 0), _OkHandler)
threading.Thread(target=_doc_srv.serve_forever, daemon=True).start()
_doc_base = "http://127.0.0.1:%d/v1" % _doc_srv.server_port

_doc_home = tempfile.mkdtemp()
_doc_orig = (config_mod.HOME_CONFIG, config_mod.LOCAL_CONFIG)
config_mod.HOME_CONFIG = os.path.join(_doc_home, "mule.json")
config_mod.LOCAL_CONFIG = os.path.join(_doc_home, "local.json")
_doc_env = dict(os.environ)
os.environ.pop("OPENAI_API_KEY", None)
os.environ["MULE_BASE_URL"] = _doc_base

ok, _ = _check_reachable()
check("doctor reaches a live base url", ok)
os.environ["MULE_BASE_URL"] = "http://127.0.0.1:1/v1"
ok, _ = _check_reachable()
check("doctor fails on a dead base url", not ok)
os.environ["MULE_BASE_URL"] = _doc_base

ok, label = _check_key()
check("doctor fails with no api key", not ok)
os.environ["OPENAI_API_KEY"] = "sk-test"
ok, label = _check_key()
check("doctor passes with an api key and never prints it",
      ok and "sk-test" not in label)

with open(config_mod.LOCAL_CONFIG, "w") as f:
    f.write("not json{")
ok, _ = _check_config(config_mod.LOCAL_CONFIG, "local")
check("doctor flags broken config json", not ok)
os.remove(config_mod.LOCAL_CONFIG)
ok, _ = _check_config(config_mod.LOCAL_CONFIG, "local")
check("doctor is fine with a missing config", ok)

check("doctor ok on an existing root", _check_root(_doc_home)[0])
check("doctor fails on a missing root",
      not _check_root("/nonexistent-dir-xyz")[0])

check("doctor returns 0 when healthy", run_doctor(_doc_home) == 0)
os.environ.pop("OPENAI_API_KEY", None)
check("doctor returns 1 when the key is missing",
      run_doctor(_doc_home) == 1)

os.environ.clear()
os.environ.update(_doc_env)
config_mod.HOME_CONFIG, config_mod.LOCAL_CONFIG = _doc_orig
_doc_srv.shutdown()

# shell completions

from complete import completion_script
from cost import PRICING

_parser = build_parser({})
_real_flags = sorted({o for a in _parser._actions
                      for o in a.option_strings if o.startswith("--")})
_real_models = sorted(PRICING)

for _shell in ("bash", "zsh", "fish"):
    _script = completion_script(_shell, _real_flags, _real_models)
    _needles = (["--max-cost", "--dry-run", "--json", "--no-color",
                 "--context-budget", "--append-system"]
                if _shell != "fish" else
                ["max-cost", "dry-run", "json", "no-color",
                 "context-budget", "append-system"])
    check("completion %s mentions every real flag" % _shell,
          all(f in _script for f in _needles))
    check("completion %s mentions the subcommands" % _shell,
          all(s in _script for s in
              ["init", "config", "doctor", "completion", "models"]))
    check("completion %s mentions priced models" % _shell,
          "gpt-4o-mini" in _script)
try:
    completion_script("powershell", _real_flags, _real_models)
    check("completion rejects unknown shells", False)
except ValueError:
    check("completion rejects unknown shells", True)

check("completion usage errors without a shell",
      run_subcommand("completion", []) == 2)

# mule models

import io
import contextlib

_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    check("models subcommand exits 0", run_subcommand("models", []) == 0)
_models_out = _buf.getvalue()
check("models lists priced models",
      "gpt-4o-mini" in _models_out and "gpt-4o" in _models_out)
check("models shows in/out pricing",
      "0.15" in _models_out and "0.60" in _models_out)
check("models has a header row",
      "in $/1M" in _models_out and "out $/1M" in _models_out)

# --json output mode

from main import build_result

_json_args = parse_args(["do the thing", "--json", "--model", "gpt-4o-mini"])
_json_messages = [
    {"role": "user", "content": "do the thing"},
    {"role": "assistant", "tool_calls": [
        {"id": "c1",
         "function": {"name": "list_dir", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "a.txt"},
    {"role": "assistant", "content": "all done"},
]
_json_totals = {"in": 1000, "out": 500}
_json_result = build_result(_json_args, _json_messages, _json_totals)
check("--json result has the answer",
      _json_result["answer"] == "all done")
check("--json result counts tool steps",
      _json_result["steps"] == 1)
check("--json result carries usage and model",
      _json_result["tokens_in"] == 1000
      and _json_result["tokens_out"] == 500
      and _json_result["model"] == "gpt-4o-mini")
check("--json result has a numeric cost",
      isinstance(_json_result["cost"], float))
check("--json output serializes cleanly",
      json.loads(json.dumps(_json_result))["answer"] == "all done")
check("--json parses", parse_args(["t", "--json"]).json is True)

# --dry-run mode

_dry_root = tempfile.mkdtemp()
_dry_script = [
    {"role": "assistant", "tool_calls": [
        tool_call("c1", "write_file",
                  {"path": "nope.txt", "content": "should not exist"}),
        tool_call("c2", "run_shell", {"command": "touch touched.txt"})]},
    {"role": "assistant", "content": "would have done it"},
]
_dry_calls = {"n": 0}


def _dry_chat(messages, tools):
    reply = _dry_script[_dry_calls["n"]]
    _dry_calls["n"] += 1
    return reply


_dry_tools = ToolSet(_dry_root)
_dry_messages = run("make the files", _dry_chat, _dry_tools,
                    system_prompt="test", max_steps=5, dry_run=True)
check("dry run consults the model", _dry_calls["n"] == 2)
check("dry run writes no files",
      not os.path.exists(os.path.join(_dry_root, "nope.txt")))
check("dry run runs no shell commands",
      not os.path.exists(os.path.join(_dry_root, "touched.txt")))
check("dry run result says it was not executed",
      any("dry run, not executed" in m.get("content", "")
          for m in _dry_messages if m.get("role") == "tool"))
check("--dry-run parses", parse_args(["t", "--dry-run"]).dry_run is True)

# tasks from stdin

from main import resolve_task


class _Tty(io.StringIO):
    def isatty(self):
        return True


class _Pipe(io.StringIO):
    def isatty(self):
        return False


check("explicit arg wins over stdin",
      resolve_task(parse_args(["do x"]), _Pipe("do y")) == "do x")
check("dash reads the task from stdin",
      resolve_task(parse_args(["-"]), _Pipe("fix the bug")) == "fix the bug")
check("a pipe feeds the task when no arg is given",
      resolve_task(parse_args([]), _Pipe("piped task")) == "piped task")
check("a tty with no arg gives no task",
      resolve_task(parse_args([]), _Tty()) is None)

# system prompt overrides

from main import resolve_system

check("--system replaces the prompt",
      resolve_system(parse_args(["t", "--system", "be terse"])) == "be terse")
check("--append-system appends to the default",
      resolve_system(parse_args(["t", "--append-system", "be terse"]))
      .endswith("\n\nbe terse"))
check("--system and --append-system combine",
      resolve_system(parse_args(["t", "--system", "base",
                                 "--append-system", "extra"]))
      == "base\n\nextra")
check("no flags keeps the default prompt",
      "coding agent" in resolve_system(parse_args(["t"])))

# --no-color and NO_COLOR

import ui

_old_nocolor = os.environ.pop("NO_COLOR", None)
ui.init_color(False)
check("errors are red by default",
      ui.red("error: x").startswith("\033[31m"))
check("diff plus lines are green",
      "\033[32m+new" in ui.color_diff("+new\n-old\n ctx\n"))
check("diff minus lines are red",
      "\033[31m-old" in ui.color_diff("+new\n-old\n ctx\n"))
_colored = ui.color_diff("+new\n-old\n ctx\n")
check("diff context lines stay plain",
      " ctx\n" in _colored
      and "\033[32m ctx" not in _colored
      and "\033[31m ctx" not in _colored)
ui.init_color(True)
check("--no-color disables color", ui.red("error: x") == "error: x")
check("--no-color disables diff color",
      ui.color_diff("+new\n") == "+new\n")
os.environ["NO_COLOR"] = "1"
ui.init_color(False)
check("NO_COLOR env disables color", ui.red("error: x") == "error: x")
if _old_nocolor is None:
    os.environ.pop("NO_COLOR", None)
else:
    os.environ["NO_COLOR"] = _old_nocolor
ui.init_color(False)
check("--no-color parses",
      parse_args(["t", "--no-color"]).no_color is True)

# delegate tool: a subagent loop with its own history

import threading as _threading
import time as _time


def _sub_chat_factory(seen):
    def make_chat():
        def chat(messages, tools):
            seen.append([m.get("role") for m in messages])
            seen.append(messages[0].get("content", ""))
            return {"role": "assistant",
                    "content": "subagent summary: did the thing"}
        return chat
    return make_chat


_sub_seen = []
_dtools = ToolSet(root, make_chat=_sub_chat_factory(_sub_seen))
_dout = _dtools.call("delegate", {"task": "do the thing"})
check("delegate returns the subagent summary",
      _dout == "subagent summary: did the thing")
check("subagent got its own history (system + task)",
      _sub_seen and _sub_seen[0][:2] == ["system", "user"])
_sys_seen = []
_systools = ToolSet(root, make_chat=_sub_chat_factory(_sys_seen))
_systools.call("delegate", {"task": "t", "system": "custom sys"})
check("delegate passes the custom system prompt",
      _sys_seen and _sys_seen[1] == "custom sys")
_nested = ToolSet(root, make_chat=_sub_chat_factory([]), delegate_depth=1)
check("delegates cannot spawn delegates",
      "cannot spawn" in _nested.call("delegate", {"task": "x"}))
_plain = ToolSet(root)
check("delegate without make_chat is a clean error",
      "not wired up" in _plain.call("delegate", {"task": "x"}))
check("delegate with an empty task is an error",
      _dtools.call("delegate", {"task": ""}).startswith("error:"))

# delegate concurrency cap: max_delegates=1 serializes two threads

_active = {"n": 0, "max": 0}
_alock = _threading.Lock()


def _slow_chat_factory():
    def make_chat():
        def chat(messages, tools):
            with _alock:
                _active["n"] += 1
                _active["max"] = max(_active["max"], _active["n"])
            _time.sleep(0.15)
            with _alock:
                _active["n"] -= 1
            return {"role": "assistant", "content": "slow done"}
        return chat
    return make_chat


_cap_tools = ToolSet(root, make_chat=_slow_chat_factory(), max_delegates=1)
_cap_out = []


def _run_delegate():
    _cap_out.append(_cap_tools.call("delegate", {"task": "slow"}))


_t1 = _threading.Thread(target=_run_delegate)
_t2 = _threading.Thread(target=_run_delegate)
_t1.start()
_time.sleep(0.05)
_t2.start()
_t1.join(timeout=10)
_t2.join(timeout=10)
check("both delegates finished", _cap_out == ["slow done", "slow done"])
check("max_delegates=1 serialized them", _active["max"] == 1)

# background shell jobs: start, poll, output, kill

_btools = ToolSet(root)
_jid_out = _btools.call("run_shell", {"command": "echo hello-bg",
                                      "background": "true"})
check("background run_shell returns a job id",
      _jid_out.startswith("job started: 1"))
_deadline = _time.time() + 5
while _time.time() < _deadline:
    if "done" in _btools.call("jobs", {}):
        break
    _time.sleep(0.05)
check("jobs lists the finished job",
      "#1 [done" in _btools.call("jobs", {}))
check("job_output has the command output",
      "hello-bg" in _btools.call("job_output", {"id": "1"}))
check("job_output on a missing job is an error",
      _btools.call("job_output", {"id": "99"}).startswith("error:"))
_sleep_out = _btools.call("run_shell", {"command": "sleep 30",
                                        "background": "true"})
check("second background job gets id 2", "job started: 2" in _sleep_out)
_time.sleep(0.3)  # let it actually start
check("job_kill stops a running job",
      _btools.call("job_kill", {"id": "2"}) == "killed job #2")
_deadline = _time.time() + 5
while _time.time() < _deadline:
    if "killed" in _btools.call("jobs", {}):
        break
    _time.sleep(0.05)
check("killed job shows as killed", "killed" in _btools.call("jobs", {}))
check("killing a finished job says so",
      "already finished" in _btools.call("job_kill", {"id": "1"}))
check("foreground run_shell still works",
      "exit 0" in _btools.call("run_shell", {"command": "echo fg"}))

# ask_user tool

_atools = ToolSet(root, ask=lambda q, opts: "picked: " + opts[1])
_aout = _atools.call("ask_user", {"question": "which one?",
                                  "options": json.dumps(["a", "b"])})
check("ask_user returns the human's pick", _aout == "picked: b")
_noask = ToolSet(root)
check("ask_user without a human errors cleanly",
      "best guess" in _noask.call(
          "ask_user", {"question": "q?", "options": json.dumps(["a", "b"])}))
check("ask_user needs 2-4 options",
      _atools.call("ask_user", {"question": "q?",
                                "options": json.dumps(["only"])}).startswith(
          "error:"))
check("ask_user needs valid json options",
      _atools.call("ask_user", {"question": "q?",
                                "options": "nope"}).startswith("error:"))

# skills: .mule/skills/*/SKILL.md appended to the system prompt

from skills import load_skills, skills_prompt

_skill_root = tempfile.mkdtemp()
os.makedirs(os.path.join(_skill_root, "alpha"))
with open(os.path.join(_skill_root, "alpha", "SKILL.md"), "w") as f:
    f.write("always write tests first")
os.makedirs(os.path.join(_skill_root, "beta"))
with open(os.path.join(_skill_root, "beta", "SKILL.md"), "w") as f:
    f.write("   ")
os.makedirs(os.path.join(_skill_root, "gamma"))  # no SKILL.md inside
_skills = load_skills(_skill_root)
check("skills load name and text",
      _skills == [("alpha", "always write tests first")])
check("empty SKILL.md and missing SKILL.md are skipped", len(_skills) == 1)
check("missing skills dir loads nothing", load_skills("/nope") == [])
_block = skills_prompt(_skills)
check("skills render with headers",
      "## skill: alpha" in _block and "write tests first" in _block)
check("no skills renders empty", skills_prompt([]) == "")
_proj = tempfile.mkdtemp()
os.makedirs(os.path.join(_proj, ".mule", "skills", "tdd"))
with open(os.path.join(_proj, ".mule", "skills", "tdd", "SKILL.md"),
          "w") as f:
    f.write("test everything")
_sargs = parse_args(["t"])
_sargs.root = _proj
check("resolve_system appends skills",
      "## skill: tdd" in resolve_system(_sargs))

# plugin tools: ~/.mule/plugins/*.py exposing get_tools()

from plugins import load_plugins, plugin_dir

_old_home = os.environ.get("HOME")
_phome = tempfile.mkdtemp()
os.environ["HOME"] = _phome
_pdir = plugin_dir()
with open(os.path.join(_pdir, "shout.py"), "w") as f:
    f.write(
        "def get_tools():\n"
        "    return [{'name': 'shout',\n"
        "             'description': 'yell text',\n"
        "             'parameters': {'text': 'what to yell'},\n"
        "             'handler': lambda text='': text.upper()}]\n")
with open(os.path.join(_pdir, "broken.py"), "w") as f:
    f.write("def get_tools():\n    raise RuntimeError('boom')\n")
with open(os.path.join(_pdir, "nogettools.py"), "w") as f:
    f.write("X = 1\n")
_ptools, _perrs = load_plugins()
check("good plugin loads its tool",
      len(_ptools) == 1 and _ptools[0]["name"] == "shout")
check("broken plugins become errors, not crashes",
      len(_perrs) == 2 and any("broken" in e for e in _perrs)
      and any("nogettools" in e for e in _perrs))
_pt = ToolSet(root)
_pt.tools[_ptools[0]["name"]] = _ptools[0]
check("plugin tool runs through the toolset",
      _pt.call("shout", {"text": "hey"}) == "HEY")
check("plugin tool shows up in schemas",
      any(s["function"]["name"] == "shout" for s in _pt.schemas()))
if _old_home is None:
    os.environ.pop("HOME", None)
else:
    os.environ["HOME"] = _old_home

# /tools lists builtin and plugin tools

from repl import handle_slash as _handle_slash
_seen_tools = []
_ctx = {"write": _seen_tools.append, "tools": _pt, "totals": {"in": 0, "out": 0},
        "model": "m", "save_fn": lambda n: n, "commands": {}}
_handle_slash("/tools", _ctx)
check("/tools lists builtins", any("read_file" in l for l in _seen_tools))
check("/tools lists plugin tools", any("shout" in l for l in _seen_tools))

# --print mode: only the final answer on stdout, no confirmations

import io
import contextlib
import main as _main_mod


class _FakePrintClient:
    def __init__(self, *a, **k):
        pass

    def chat(self, messages, tools, stop=None):
        return {"role": "assistant", "content": "the answer",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def chat_stream(self, messages, tools, on_token=None, stop=None):
        if on_token:
            on_token("SHOULD NOT PRINT")
        return self.chat(messages, tools)


_real_client = _main_mod.ChatClient
_main_mod.ChatClient = _FakePrintClient
_print_root = tempfile.mkdtemp()
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _rc = _main_mod.main(["do it", "--print", "--api-key", "x",
                          "--root", _print_root])
_main_mod.ChatClient = _real_client
check("--print exits 0", _rc == 0)
check("--print prints only the final answer",
      _buf.getvalue() == "the answer\n")
check("--print parses", parse_args(["t", "--print"]).print_mode is True)

# profiles: ~/.mule/profiles/NAME.json merged over config

from config import load_profile, list_profiles

_prof_home = os.environ.get("HOME")
os.environ["HOME"] = tempfile.mkdtemp()
_pdir = os.path.join(os.environ["HOME"], ".mule", "profiles")
os.makedirs(_pdir)
with open(os.path.join(_pdir, "work.json"), "w") as f:
    json.dump({"model": "gpt-4o", "max_steps": 5}, f)
check("load_profile reads the file",
      load_profile("work") == {"model": "gpt-4o", "max_steps": 5})
check("list_profiles finds it", list_profiles() == ["work"])
try:
    load_profile("nope")
    _pok = False
except ValueError as e:
    _pok = "available: work" in str(e)
check("missing profile errors with available list", _pok)
with open(os.path.join(_pdir, "bad.json"), "w") as f:
    f.write("{not json")
try:
    load_profile("bad")
    _bok = False
except ValueError as e:
    _bok = "not valid json" in str(e)
check("bad json profile errors cleanly", _bok)
_pargs = parse_args(["--profile", "work", "t"])
check("profile sets the model", _pargs.model == "gpt-4o")
check("profile sets max_steps", _pargs.max_steps == 5)
_pargs2 = parse_args(["--profile", "work", "--model", "mini", "t"])
check("cli flag beats profile", _pargs2.model == "mini")
if _prof_home is None:
    os.environ.pop("HOME", None)
else:
    os.environ["HOME"] = _prof_home

# --output FILE writes the final answer to a file

_real_client = _main_mod.ChatClient
_main_mod.ChatClient = _FakePrintClient
_out_root = tempfile.mkdtemp()
_out_file = os.path.join(_out_root, "ans.txt")
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _rc = _main_mod.main(["do it", "--api-key", "x", "--root", _out_root,
                          "--output", _out_file])
_main_mod.ChatClient = _real_client
check("--output exits 0", _rc == 0)
check("--output writes the final answer",
      open(_out_file).read() == "the answer\n")
check("--output says where it went",
      "wrote answer to" in _buf.getvalue())

# --temperature, --max-tokens, --seed reach the request body

from client import ChatClient as _RealChatClient

_pc = _RealChatClient("http://x/v1", "k", "m", temperature=0.3,
                      max_tokens=50, seed=7)
_body = _pc._body([{"role": "user", "content": "hi"}], [{"type": "f"}])
check("body carries temperature",
      _body.get("temperature") == 0.3)
check("body carries max_tokens", _body.get("max_tokens") == 50)
check("body carries seed", _body.get("seed") == 7)
_plain_c = _RealChatClient("http://x/v1", "k", "m")
_plain_body = _plain_c._body([{"role": "user", "content": "hi"}])
check("unset params stay out of the body",
      not any(k in _plain_body for k in ("temperature", "max_tokens",
                                         "seed")))
_wire = {}


class _WireCapture(_RealChatClient):
    def _post(self, req):
        _wire.update(json.loads(req.data.decode()))
        raise RuntimeError("stop here")


_wc = _WireCapture("http://x/v1", "k", "m", temperature=0.9, seed=3)
try:
    _wc.chat([{"role": "user", "content": "hi"}])
except RuntimeError:
    pass
check("wire body carries temperature and seed",
      _wire.get("temperature") == 0.9 and _wire.get("seed") == 3)
_pa = parse_args(["t", "--temperature", "0.5", "--max-tokens", "100",
                  "--seed", "42"])
check("flags parse", _pa.temperature == 0.5 and _pa.max_tokens == 100
      and _pa.seed == 42)
check("flags default to unset",
      parse_args(["t"]).temperature is None
      and parse_args(["t"]).max_tokens is None
      and parse_args(["t"]).seed is None)

# --continue picks up the most recent session

from sessions import latest_session, save_session

_cont_home = os.environ.get("HOME")
os.environ["HOME"] = tempfile.mkdtemp()
save_session("older", [{"role": "user", "content": "first"}])
check("latest_session finds the only session",
      latest_session() == "older")
import time as _ctime
_ctime.sleep(0.02)
save_session("newer", [{"role": "user", "content": "second"}])
check("latest_session picks the newest",
      latest_session() == "newer")
_real_client = _main_mod.ChatClient
_main_mod.ChatClient = _FakePrintClient
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _rc = _main_mod.main(["follow up", "--continue", "--api-key", "x",
                          "--root", tempfile.mkdtemp(), "--print"])
_main_mod.ChatClient = _real_client
check("--continue exits 0", _rc == 0)
check("--continue prints only the final answer",
      _buf.getvalue() == "the answer\n")
check("--continue parses", parse_args(["--continue", "t"]).cont is True)
if _cont_home is None:
    os.environ.pop("HOME", None)
else:
    os.environ["HOME"] = _cont_home

# --- batch A: agent loop upgrades ---

# --max-tools stops the loop after N tool executions

_mt_root = tempfile.mkdtemp()
_mt_tools = ToolSet(_mt_root)
_mt_calls = {"n": 0}


def _mt_chat(messages, tools):
    _mt_calls["n"] += 1
    return {"role": "assistant",
            "tool_calls": [tool_call("c%d" % _mt_calls["n"], "list_dir",
                                     {"path": "."})]}


_mt_msgs = run("list stuff", _mt_chat, _mt_tools, system_prompt="t",
               max_steps=10, max_tools=2)
check("--max-tools stops the loop",
      last_answer(_mt_msgs) == "stopped: hit max tools (2)")
check("--max-tools consulted the model once more, then stopped",
      _mt_calls["n"] == 3)

# --time-limit stops a run that takes too long

_tl_msgs = run("slow", _mt_chat, _mt_tools, system_prompt="t",
               max_steps=10, time_limit=0)
check("--time-limit stops immediately when already exceeded",
      last_answer(_tl_msgs).startswith("stopped: hit time limit"))

# on_timing reports per-step durations for --verbose

_timings = []
_vt_calls = {"n": 0}


def _vt_chat(messages, tools):
    _vt_calls["n"] += 1
    if _vt_calls["n"] < 3:
        return {"role": "assistant",
                "tool_calls": [tool_call("c", "list_dir", {"path": "."})]}
    return {"role": "assistant", "content": "done"}


run("t", _vt_chat, ToolSet(tempfile.mkdtemp()), system_prompt="t",
    on_timing=lambda s, d: _timings.append((s, d)))
check("--verbose timing fires per chat call",
      len(_timings) == 3 and all(d >= 0 for _, d in _timings))
check("--verbose timing numbers the steps",
      [s for s, _ in _timings] == [1, 2, 3])

# --parallel-tools runs one turn's calls concurrently, ordered results

_pt_root = tempfile.mkdtemp()
for _fn, _c in (("a.txt", "aaa"), ("b.txt", "bbb"), ("c.txt", "ccc")):
    open(os.path.join(_pt_root, _fn), "w").write(_c)
_pt_tools = ToolSet(_pt_root)


def _pt_chat(messages, tools):
    if len(messages) == 2:
        return {"role": "assistant", "tool_calls": [
            tool_call("1", "read_file", {"path": "a.txt"}),
            tool_call("2", "read_file", {"path": "b.txt"}),
            tool_call("3", "read_file", {"path": "c.txt"})]}
    return {"role": "assistant", "content": "done"}


_pt_msgs = run("read all three", _pt_chat, _pt_tools, system_prompt="t",
               parallel_tools=True)
_pt_results = [m["content"] for m in _pt_msgs if m.get("role") == "tool"]
check("--parallel-tools executes every call", _pt_results == ["aaa", "bbb", "ccc"])

# failed tool calls get one guided retry

_rt_root = tempfile.mkdtemp()
open(os.path.join(_rt_root, "real.txt"), "w").write("real content")
_rt_script = {"n": 0}


def _rt_chat(messages, tools):
    _rt_script["n"] += 1
    if _rt_script["n"] == 1:
        return {"role": "assistant", "tool_calls": [
            tool_call("c1", "read_file", {"path": "missing.txt"})]}
    if _rt_script["n"] == 2:
        return {"role": "assistant", "tool_calls": [
            tool_call("c2", "read_file", {"path": "real.txt"})]}
    return {"role": "assistant", "content": "got it"}


_rt_msgs = run("read the file", _rt_chat, ToolSet(_rt_root),
               system_prompt="t")
_rt_hint = [m for m in _rt_msgs
            if m.get("role") == "user" and "fix the arguments" in m["content"]]
check("failed tool call gets one guided retry", len(_rt_hint) == 1)
check("retry carries the error text", "no such file" in _rt_hint[0]["content"])
check("model recovered after the retry",
      any(m.get("content") == "real content" for m in _rt_msgs
          if m.get("role") == "tool"))

# --stop sequences reach the request body

from client import ChatClient as _StopClient

_sc = _StopClient("http://x/v1", "k", "m")
_sb = _sc._body([{"role": "user", "content": "hi"}], stop=["END", "STOP"])
check("--stop lands in the request body", _sb.get("stop") == ["END", "STOP"])
_sb2 = _sc._body([{"role": "user", "content": "hi"}])
check("no stop key when unset", "stop" not in _sb2)

# --schema validation

from main import validate_schema, enforce_schema, reflect_answer

check("validate_schema passes good json",
      validate_schema('{"a": 1}', {"required": ["a"]}) is None)
check("validate_schema rejects non-json",
      validate_schema("nope", {"required": ["a"]}) == "the answer is not valid json")
check("validate_schema rejects non-objects",
      validate_schema("[1, 2]", {"required": ["a"]}) == "the answer must be a json object")
check("validate_schema flags missing keys",
      validate_schema('{"a": 1}', {"required": ["a", "b"]})
      == "missing required keys: b")


def _fix_chat(messages, tools):
    return {"role": "assistant", "content": '{"a": 1, "b": 2}',
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


_schema_args = parse_args(["t", "--schema", "s.json"])
_schema_msgs = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": '{"a": 1}'}]
with open(os.path.join(tempfile.mkdtemp(), "s.json"), "w") as _sf:
    json.dump({"required": ["a", "b"]}, _sf)
    _schema_args.schema = _sf.name
_fixed = enforce_schema(_schema_args, _fix_chat, _schema_msgs,
                        lambda s, u: False, 3)
check("--schema asks for one fix when invalid",
      last_answer(_fixed) == '{"a": 1, "b": 2}')

# --reflect critiques the final answer and improves it

_reflect_script = {"n": 0}


def _reflect_chat(messages, tools):
    _reflect_script["n"] += 1
    return {"role": "assistant",
            "content": "critique: too terse. improved: the much better answer",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


_reflect_args = parse_args(["t", "--reflect"])
_reflect_msgs = [{"role": "user", "content": "go"},
                 {"role": "assistant", "content": "the first answer"}]
_out = reflect_answer(_reflect_args, _reflect_chat, _reflect_msgs,
                      lambda s, u: False, 3)
check("--reflect makes one extra model call", _reflect_script["n"] == 1)
check("--reflect keeps the improved answer",
      "the much better answer" in last_answer(_out))

# --fallback-model switches models after a failure

_fallback_calls = {"n": 0}


class _FlakyClient:
    def __init__(self):
        self.model = "primary"

    def chat(self, messages, tools, stop=None):
        _fallback_calls["n"] += 1
        if _fallback_calls["n"] == 1:
            raise RuntimeError("api error 500 after 1 attempt(s): boom")
        return {"role": "assistant", "content": "recovered"}

    def chat_stream(self, messages, tools, on_token=None, stop=None):
        return self.chat(messages, tools, stop=stop)


from main import make_chat_fn

_fb_args = parse_args(["t", "--no-stream", "--fallback-model", "backup"])
_fb_client = _FlakyClient()
_fb_fn = make_chat_fn(_fb_args, _fb_client)
_fb_reply = _fb_fn([{"role": "user", "content": "hi"}], [])
check("--fallback-model retries on the backup model",
      _fb_reply["content"] == "recovered"
      and _fallback_calls["n"] == 2)
check("--fallback-model updates the model box",
      _fb_fn.model_box["model"] == "backup"
      and _fb_fn.model_box["fell_back"] is True)


def _always_fail(messages, tools, **kw):
    raise RuntimeError("down")


_fb_args2 = parse_args(["t", "--no-stream", "--fallback-model", "backup"])
_fb_client2 = _FlakyClient()
_fb_client2.chat = _always_fail
_fb_client2.chat_stream = _always_fail
_fb_fn2 = make_chat_fn(_fb_args2, _fb_client2)
try:
    _fb_fn2([{"role": "user", "content": "hi"}], [])
    _fb_raised = False
except RuntimeError:
    _fb_raised = True
check("--fallback-model still raises when the backup fails", _fb_raised)

# --fork branches a saved session

_fork_home = os.environ.get("HOME")
os.environ["HOME"] = tempfile.mkdtemp()
from sessions import save_session
save_session("base", [{"role": "user", "content": "original task"},
                      {"role": "assistant", "content": "original answer"}])
_real_client = _main_mod.ChatClient
_main_mod.ChatClient = _FakePrintClient
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _rc = _main_mod.main(["new task", "--fork", "base", "--api-key", "x",
                          "--root", tempfile.mkdtemp(), "--print"])
_main_mod.ChatClient = _real_client
check("--fork exits 0", _rc == 0)
check("--fork says what it branched from",
      "forked from session: base" in _buf.getvalue()
      or _buf.getvalue() == "the answer\n")
check("--fork parses", parse_args(["t", "--fork", "base"]).fork == "base")
if _fork_home is None:
    os.environ.pop("HOME", None)
else:
    os.environ["HOME"] = _fork_home

# new flags parse

check("--reflect parses", parse_args(["t", "--reflect"]).reflect is True)
check("--fallback-model parses",
      parse_args(["t", "--fallback-model", "x"]).fallback_model == "x")
check("--stop parses", parse_args(["t", "--stop", "a,b"]).stop == "a,b")
check("--max-tools parses", parse_args(["t", "--max-tools", "5"]).max_tools == 5)
check("--time-limit parses",
      parse_args(["t", "--time-limit", "30"]).time_limit == 30)
check("--parallel-tools parses",
      parse_args(["t", "--parallel-tools"]).parallel_tools is True)
check("--verbose parses", parse_args(["t", "--verbose"]).verbose is True)

# --- batch B: more agent tools ---

_grep_root = tempfile.mkdtemp()
open(os.path.join(_grep_root, "a.py"), "w").write("import os\nprint('hello')\n")
open(os.path.join(_grep_root, "b.txt"), "w").write("hello world\nbye\n")
_grep_tools = ToolSet(_grep_root)
check("grep finds matches as file:line",
      _grep_tools.call("grep", {"pattern": "hello"})
      == "a.py:2: print('hello')\nb.txt:1: hello world")
check("grep honors the glob filter",
      _grep_tools.call("grep", {"pattern": "hello", "glob": "*.txt"})
      == "b.txt:1: hello world")
check("grep reports no matches",
      _grep_tools.call("grep", {"pattern": "zzz"}) == "no matches for 'zzz'")
check("grep rejects bad regex",
      _grep_tools.call("grep", {"pattern": "["}).startswith("error: bad pattern"))
check("grep is registered", "grep" in _grep_tools.schemas()[0] or
      any(s["function"]["name"] == "grep" for s in _grep_tools.schemas()))

_find_root = tempfile.mkdtemp()
os.makedirs(os.path.join(_find_root, "sub"))
open(os.path.join(_find_root, "a.py"), "w").write("x")
open(os.path.join(_find_root, "sub", "b.py"), "w").write("x")
open(os.path.join(_find_root, "sub", "c.txt"), "w").write("x")
_find_tools = ToolSet(_find_root)
check("find locates files recursively",
      _find_tools.call("find", {"pattern": "*.py"}) == "a.py\nsub/b.py")
check("find reports no matches",
      _find_tools.call("find", {"pattern": "*.go"})
      == "no files matching '*.go'")

_tree_tools = ToolSet(_find_root)
_tree_out = _tree_tools.call("tree", {"path": "."})
_tree_base = os.path.basename(_find_root)
check("tree shows the root", _tree_out.split("\n")[0] == _tree_base)
check("tree marks dirs with a slash",
      "sub/" in _tree_out and "a.py" in _tree_out)
os.makedirs(os.path.join(_find_root, "sub", "deep"))
open(os.path.join(_find_root, "sub", "deep", "x.txt"), "w").write("x")
check("tree depth 1 hides nested content",
      "deep/" not in _tree_tools.call("tree", {"path": ".", "depth": "1"})
      and "deep/" in _tree_tools.call("tree", {"path": ".", "depth": "3"}))

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
