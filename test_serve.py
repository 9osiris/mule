"""tests for mule serve: the local web chat ui. hermetic: fake api
client, temp session dir, server on 127.0.0.1 with a free port."""
import http.client
import json
import os
import sys
import tempfile
import threading
import types
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serve
import sessions

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL: %s" % name)


class FakeClient:
    # turns: ("tokens", [t...]) streams tokens then answers with them
    # joined; ("tools", (calls,)) answers with tool calls.
    def __init__(self, turns):
        self.turns = list(turns)

    def chat_stream(self, messages, tools, on_token=None, stop=None):
        kind, val = self.turns.pop(0)
        if kind == "tokens":
            for t in val:
                on_token(t)
            return {"role": "assistant", "content": "".join(val)}
        if kind == "tools":
            return {"role": "assistant", "content": "",
                    "tool_calls": val}
        raise AssertionError("fake client out of turns")


class FakeTools:
    def __init__(self):
        self.disabled = set()

    def schemas(self):
        return [{"type": "function",
                 "function": {"name": "list_dir", "description": "x"}}]

    def call(self, name, args):
        return "fake result for " + name

    def disable_tool(self, name):
        self.disabled.add(name)
        return True

    def enable_tool(self, name):
        self.disabled.discard(name)
        return True


def make_args():
    return types.SimpleNamespace(model="gpt-4o-mini", root="/tmp",
                                 max_steps=5, version="test",
                                 base_url="http://127.0.0.1:1/v1",
                                 readonly=False, allow_network=False,
                                 no_network=False, deny_tools="",
                                 allow_tools="",
                                 context_budget=100000)


class Server:
    def __init__(self, turns):
        self.tmp = tempfile.mkdtemp(prefix="mule-serve-test-")
        self._old_dir = sessions.session_dir
        sessions.session_dir = lambda: self.tmp
        ctx = serve.Ctx(make_args(), FakeClient(turns), FakeTools(),
                        "test system prompt")
        self.httpd = serve.make_server(ctx, 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        sessions.session_dir = self._old_dir

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = dict(resp.getheaders())
        conn.close()
        return resp.status, body, headers

    def post(self, path, obj):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=30)
        data = json.dumps(obj).encode()
        conn.request("POST", path, data,
                     {"Content-Type": "application/json",
                      "Content-Length": str(len(data))})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body

    def chat_events(self, message, session_id=None):
        status, body = self.post("/api/chat", {"message": message,
                                               "session_id": session_id})
        events = []
        if status == 200:
            for chunk in body.decode().split("\n\n"):
                for line in chunk.splitlines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:].strip()))
        return status, events


def test_index():
    s = Server([("tokens", ["hi"])])
    try:
        status, body, headers = s.get("/")
        check("index 200", status == 200)
        check("index is html", "text/html" in headers.get("Content-Type", ""))
        check("index has title", b"<title>mule</title>" in body)
        check("index has sidebar", b'id="sess"' in body)
        check("index has composer", b'id="in"' in body)
        check("index has file tree", b'id="tree"' in body)
        check("index shows brand", b"mule 3.1" in body or b"id=\"brandcap\"" in body)
        check("no external scripts",
              b'src="http' not in body and b"href=\"http" not in body)
    finally:
        s.close()


def test_info():
    s = Server([("tokens", ["hi"])])
    try:
        status, body, _ = s.get("/api/info")
        info = json.loads(body)
        check("info 200", status == 200)
        check("info model", info["model"] == "gpt-4o-mini")
        check("info root absolute", os.path.isabs(info["root"]))
        check("info version", info["version"] == "test")
    finally:
        s.close()


def test_chat_stream_and_session():
    s = Server([("tokens", ["hello", " there"])])
    try:
        status, events = s.chat_events("hi")
        check("chat 200", status == 200)
        kinds = [e["type"] for e in events]
        check("chat streams tokens then done",
              kinds == ["token", "token", "done"])
        check("chat token text",
              "".join(e["text"] for e in events
                      if e["type"] == "token") == "hello there")
        done = events[-1]
        check("done has answer", done["answer"] == "hello there")
        check("done has session id", bool(done.get("session_id")))
        check("done has cost", "cost" in done)
        sid = done["session_id"]
        status, body, _ = s.get("/api/sessions")
        listed = json.loads(body)["sessions"]
        check("session listed",
              any(x["id"] == sid for x in listed))
        check("session preview",
              any(x["id"] == sid and "hi" in x["preview"]
                  for x in listed))
        status, body, _ = s.get("/api/session?id=" +
                                urllib.parse.quote(sid))
        msgs = json.loads(body)["messages"]
        roles = [m["role"] for m in msgs]
        check("session has user+assistant", roles == ["user", "assistant"])
        check("assistant content kept",
              msgs[1]["content"] == "hello there")
        check("no system leaked",
              all(m["role"] != "system" for m in msgs))
    finally:
        s.close()


def test_chat_tool_events():
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "list_dir", "arguments": "{}"}}]
    s = Server([("tools", calls), ("tokens", ["done"])])
    try:
        status, events = s.chat_events("list it")
        kinds = [e["type"] for e in events]
        check("tool event present", "tool" in kinds)
        tool_ev = next(e for e in events if e["type"] == "tool")
        check("tool event names call",
              tool_ev["calls"][0]["name"] == "list_dir")
        check("tool event carries result",
              any("fake result for list_dir" in r
                  for r in tool_ev["results"]))
        check("done after tools", kinds[-1] == "done")
        sid = events[-1]["session_id"]
        status, body, _ = s.get("/api/session?id=" +
                                urllib.parse.quote(sid))
        msgs = json.loads(body)["messages"]
        check("tool result saved",
              any(m["role"] == "tool" and "fake result" in m["content"]
                  for m in msgs))
    finally:
        s.close()


def test_chat_continues_session():
    s = Server([("tokens", ["one"]), ("tokens", ["two"])])
    try:
        _, ev1 = s.chat_events("first")
        sid = ev1[-1]["session_id"]
        _, ev2 = s.chat_events("second", session_id=sid)
        check("same session kept", ev2[-1]["session_id"] == sid)
        status, body, _ = s.get("/api/session?id=" +
                                urllib.parse.quote(sid))
        msgs = json.loads(body)["messages"]
        check("history grows",
              [m["role"] for m in msgs] ==
              ["user", "assistant", "user", "assistant"])
    finally:
        s.close()


def test_delete_session():
    s = Server([("tokens", ["hi"])])
    try:
        _, events = s.chat_events("hi")
        sid = events[-1]["session_id"]
        status, _ = s.post("/api/session/delete", {"id": sid})
        check("delete 200", status == 200)
        status, body, _ = s.get("/api/session?id=" +
                                urllib.parse.quote(sid))
        check("deleted session 404s", status == 404)
        status, _ = s.post("/api/session/delete", {"id": "nope"})
        check("delete missing 404", status == 404)
    finally:
        s.close()


def test_errors():
    s = Server([("tokens", ["hi"])])
    try:
        status, _ = s.post("/api/chat", {"message": "   "})
        check("empty message 400", status == 400)
        status, _, _ = s.get("/api/session?id=nope")
        check("missing session 404", status == 404)
        status, _, _ = s.get("/nope")
        check("unknown path 404", status == 404)
        # the busy lock: hold it, a second chat must 409
        serve._chat_lock.acquire()
        try:
            status, body = s.post("/api/chat", {"message": "hi"})
            check("busy chat 409", status == 409)
        finally:
            serve._chat_lock.release()
    finally:
        s.close()


def test_info_brand():
    s = Server([("tokens", ["hi"])])
    try:
        status, body, _ = s.get("/api/info")
        info = json.loads(body)
        check("info 200", status == 200)
        check("info has brand mule 3.1", info.get("brand") == "mule 3.1")
        check("info has base_url", info.get("base_url") == "http://127.0.0.1:1/v1")
        check("info readonly flag", info.get("readonly") is False)
    finally:
        s.close()


def test_tree():
    s = Server([("tokens", ["hi"])])
    try:
        root = tempfile.mkdtemp(prefix="mule-tree-")
        os.makedirs(os.path.join(root, "sub"))
        open(os.path.join(root, "a.txt"), "w").write("x")
        open(os.path.join(root, ".hidden"), "w").write("x")
        s.post("/api/root", {"path": root})
        status, body, _ = s.get("/api/tree")
        d = json.loads(body)
        names = [e["name"] for e in d["entries"]]
        check("tree 200", status == 200)
        check("tree lists file", "a.txt" in names)
        check("tree lists dir", "sub" in names)
        check("tree skips dotfiles", ".hidden" not in names)
        status, body, _ = s.get("/api/tree?dir=" + urllib.parse.quote("../.."))
        check("tree blocks escape", status == 400)
    finally:
        s.close()


def test_root_switch():
    s = Server([("tokens", ["hi"])])
    try:
        root = tempfile.mkdtemp(prefix="mule-root-")
        status, body = s.post("/api/root", {"path": root})
        d = json.loads(body)
        check("root switch 200", status == 200 and d.get("ok"))
        check("root switch echoes path", d.get("root") == root)
        status, body = s.post("/api/root", {"path": "/no/such/dir/xyz"})
        check("root rejects bad path", status == 400)
        status, body = s.post("/api/root", {"path": ""})
        check("root rejects empty", status == 400)
    finally:
        s.close()


def test_model_switch():
    s = Server([("tokens", ["hi"])])
    try:
        status, body = s.post("/api/model", {"model": "deepseek-v4-flash"})
        d = json.loads(body)
        check("model switch 200", status == 200 and d.get("ok"))
        check("model switch echoes", d.get("model") == "deepseek-v4-flash")
        _, ibody, _ = s.get("/api/info")
        check("info reflects model", json.loads(ibody)["model"] == "deepseek-v4-flash")
        status, _ = s.post("/api/model", {"model": ""})
        check("model rejects empty", status == 400)
    finally:
        s.close()


def test_readonly_toggle():
    s = Server([("tokens", ["hi"])])
    try:
        status, body = s.post("/api/readonly", {"on": True})
        d = json.loads(body)
        check("readonly on", status == 200 and d.get("readonly") is True)
        check("readonly disables writes",
              "write_file" in s.httpd.ctx.tools.disabled)
        status, body = s.post("/api/readonly", {"on": False})
        check("readonly off", json.loads(body).get("readonly") is False)
        check("readonly re-enables",
              "write_file" not in s.httpd.ctx.tools.disabled)
    finally:
        s.close()


def test_rename_session():
    s = Server([("tokens", ["hello"])])
    try:
        status, events = s.chat_events("make a session")
        sid = next(e["session_id"] for e in events if e["type"] == "done")
        status, body = s.post("/api/session/rename",
                              {"id": sid, "name": "renamed-chat"})
        check("rename 200", status == 200 and json.loads(body).get("ok"))
        _, lbody, _ = s.get("/api/sessions")
        ids = [x["id"] for x in json.loads(lbody)["sessions"]]
        check("rename took effect", "renamed-chat" in ids and sid not in ids)
        status, _ = s.post("/api/session/rename",
                           {"id": "nope", "name": "x"})
        check("rename missing 404", status == 404)
    finally:
        s.close()


def test_stop_endpoint():
    s = Server([("tokens", ["hi"])])
    try:
        status, body = s.post("/api/stop", {})
        check("stop 200", status == 200 and json.loads(body).get("ok"))
        check("stop sets the event", s.httpd.ctx.stop.is_set())
    finally:
        s.close()


def main():
    test_index()
    test_info()
    test_info_brand()
    test_tree()
    test_root_switch()
    test_model_switch()
    test_readonly_toggle()
    test_rename_session()
    test_stop_endpoint()
    test_chat_stream_and_session()
    test_chat_tool_events()
    test_chat_continues_session()
    test_delete_session()
    test_errors()
    print("%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
