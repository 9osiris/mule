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
      and len(seen["bodies"][0]["tools"]) == 4)
check("second request included the tool result",
      seen["bodies"][1]["messages"][-1]["role"] == "tool")
check("file written through the whole stack",
      open(os.path.join(root2, "srv.txt")).read() == "via server")
check("final answer came back",
      last_answer(msgs) == "wrote it through the server")

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
