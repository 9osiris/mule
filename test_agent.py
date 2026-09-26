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
      and len(seen["bodies"][0]["tools"]) == 6)
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

from main import parse_args, ask_cmd
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

from tools import BackupStore

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

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
