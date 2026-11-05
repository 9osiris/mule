"""tests for the mega serve wave: auth token, per-session
endpoints, job cancel and job stream. hermetic: fake api client,
temp session dir, server on 127.0.0.1 with a free port."""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import types
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serve
import sessions
from tools import JobStore

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)

    def chat_stream(self, messages, tools, on_token=None, stop=None):
        kind, val = self.turns.pop(0)
        if kind == "tokens":
            for t in val:
                on_token(t)
            return {"role": "assistant", "content": "".join(val)}
        raise AssertionError("fake client out of turns")


class FakeTools:
    def __init__(self, root):
        self.disabled = set()
        self.jobs = JobStore(root)

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
    def __init__(self, turns, token=None):
        self.tmp = tempfile.mkdtemp(prefix="mule-serve-test-")
        self._old_dir = sessions.session_dir
        sessions.session_dir = lambda: self.tmp
        self.root = tempfile.mkdtemp(prefix="mule-job-root-")
        ctx = serve.Ctx(make_args(), FakeClient(turns),
                        FakeTools(self.root), "test system prompt",
                        auth_token=token)
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

    def request(self, method, path, obj=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=30)
        data = json.dumps(obj).encode() if obj is not None else None
        hdrs = dict(headers or {})
        if data is not None:
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(data))
        conn.request(method, path, data, hdrs)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, obj, headers=None):
        return self.request("POST", path, obj, headers)

    def delete(self, path, headers=None):
        return self.request("DELETE", path, headers=headers)

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


def test_auth_open_without_token():
    s = Server([("tokens", ["hi"])])
    try:
        status, body = s.get("/api/info")
        check("no token configured stays open", status == 200)
        status, _ = s.post("/api/stop", {})
        check("post stays open without a token", status == 200)
    finally:
        s.close()


def test_auth_token():
    s = Server([("tokens", ["hi"])], token="secret123")
    try:
        status, _ = s.get("/api/info")
        check("api without a token is 401", status == 401)
        status, _ = s.get("/api/info?token=wrong")
        check("api with a wrong token is 401", status == 401)
        status, body = s.get("/api/info?token=secret123")
        check("api with ?token= works",
              status == 200 and json.loads(body)["model"] == "gpt-4o-mini")
        status, _ = s.get("/api/info", headers={
            "Authorization": "Bearer secret123"})
        check("api with a bearer header works", status == 200)
        status, _ = s.get("/api/info", headers={
            "Authorization": "Bearer nope"})
        check("api with a wrong bearer is 401", status == 401)
        status, _ = s.post("/api/chat", {"message": "hi"})
        check("chat without a token is 401", status == 401)
        status, _ = s.post("/api/chat?token=secret123",
                           {"message": "hi"})
        check("chat with a token works", status == 200)
        status, body = s.get("/")
        check("webui page itself stays open", status == 200)
    finally:
        s.close()


def test_session_detail_and_delete():
    s = Server([("tokens", ["hello"])])
    try:
        _, events = s.chat_events("hi there")
        sid = events[-1]["session_id"]
        status, body = s.get("/api/sessions/" +
                             urllib.parse.quote(sid))
        d = json.loads(body)
        check("session detail 200", status == 200)
        check("session detail echoes the id", d.get("id") == sid)
        check("session detail has messages",
              [m["role"] for m in d["messages"]] ==
              ["user", "assistant"])
        status, _ = s.get("/api/sessions/nope")
        check("missing session detail 404", status == 404)
        status, body = s.delete("/api/sessions/" +
                                urllib.parse.quote(sid))
        check("delete session 200",
              status == 200 and json.loads(body).get("ok"))
        status, _ = s.get("/api/sessions/" +
                          urllib.parse.quote(sid))
        check("deleted session detail 404", status == 404)
        status, _ = s.delete("/api/sessions/nope")
        check("delete missing session 404", status == 404)
        status, _ = s.delete("/api/sessions/")
        check("delete without an id is 400", status == 400)
    finally:
        s.close()


def test_session_detail_auth():
    s = Server([("tokens", ["hi"])], token="tok")
    try:
        status, _ = s.get("/api/sessions/anything")
        check("session detail needs the token", status == 401)
        status, _ = s.delete("/api/sessions/anything")
        check("session delete needs the token", status == 401)
    finally:
        s.close()


def test_job_cancel():
    s = Server([("tokens", ["hi"])])
    try:
        jid = s.httpd.ctx.tools.jobs.start(
            "exec python3 -c 'import time; time.sleep(10)'")
        status, body = s.post("/api/jobs/%d/cancel" % jid, {})
        check("cancel running job 200",
              status == 200 and json.loads(body).get("ok"))
        check("cancel marks the job killed",
              s.httpd.ctx.tools.jobs.get(jid)["killed"] is True)
        for _ in range(50):
            if s.httpd.ctx.tools.jobs.get(jid)["done"]:
                break
            time.sleep(0.1)
        check("cancelled job finishes",
              s.httpd.ctx.tools.jobs.get(jid)["done"] is True)
        status, _ = s.post("/api/jobs/%d/cancel" % jid, {})
        check("cancel of a finished job is 409", status == 409)
        status, _ = s.post("/api/jobs/9999/cancel", {})
        check("cancel of a missing job is 404", status == 404)
        status, _ = s.post("/api/jobs/abc/cancel", {})
        check("cancel with a bad id is 400", status == 400)
    finally:
        s.close()


def test_job_stream():
    s = Server([("tokens", ["hi"])])
    try:
        jid = s.httpd.ctx.tools.jobs.start("echo hello-job")
        status, body = s.get("/api/jobs/%d/stream" % jid)
        events = []
        for chunk in body.decode().split("\n\n"):
            for line in chunk.splitlines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
        check("job stream 200", status == 200)
        check("job stream ends with done",
              events and events[-1]["type"] == "done")
        check("job stream carries the output",
              any("hello-job" in (e.get("text", "") +
                                  e.get("output", ""))
                  for e in events))
        status, _ = s.get("/api/jobs/9999/stream")
        check("stream of a missing job is 404", status == 404)
        status, _ = s.get("/api/jobs/abc/stream")
        check("stream with a bad id is 400", status == 400)
    finally:
        s.close()


def test_webui_panels():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "webui.html")
    text = open(path).read()
    check("webui has a session list panel", 'id="sess"' in text)
    check("webui session panel refreshes",
          'id="sessrefresh"' in text and "/api/sessions" in text)
    check("webui has a cost display area",
          'id="costpill"' in text and 'id="costv"' in text)
    check("webui cost area shows tokens in and out",
          "in / " in text and "tokens_in" in text)
    check("webui has no em dash", "\u2014" not in text)
    check("webui has no external assets",
          'src="http' not in text and 'href="http' not in text)


def main():
    test_auth_open_without_token()
    test_auth_token()
    test_session_detail_and_delete()
    test_session_detail_auth()
    test_job_cancel()
    test_job_stream()
    test_webui_panels()
    print("%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print("FAIL: %s" % name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
