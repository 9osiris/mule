"""mule serve: a local web chat ui in the style of gpt4all.

one self-contained page (webui.html), no build step, no npm, no
external requests. the backend is stdlib only: ThreadingHTTPServer
plus server-sent events. binds to 127.0.0.1, never 0.0.0.0.
"""
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import sessions
from cost import cost_for, fmt_cost

WEBUI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "webui.html")

_chat_lock = threading.Lock()


class Ctx:
    # everything a request handler needs: parsed args, the api
    # client, the tool set, and the system prompt.
    def __init__(self, args, client, tools, system):
        self.args = args
        self.client = client
        self.tools = tools
        self.system = system


def _preview(messages):
    # first user message, squashed to one line for the sidebar
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            text = " ".join(str(m["content"]).split())
            return text[:60] + ("..." if len(text) > 60 else "")
    return "new chat"


def _public_messages(messages):
    # the transcript the browser needs: user/assistant/tool only,
    # no system prompt, no usage blobs
    out = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user",
                        "content": m.get("content", "")})
        elif role == "assistant":
            out.append({"role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": [
                            {"name": c["function"]["name"],
                             "args": c["function"].get("arguments", "")}
                            for c in m.get("tool_calls", [])]})
        elif role == "tool":
            out.append({"role": "tool", "content": m.get("content", "")})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "mule-serve/1.0"

    def log_message(self, fmt, *args):
        # one quiet line per request on stderr, not the default
        # dump of every header
        import sys
        sys.stderr.write("serve: %s\n" % (fmt % args))

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def _sse_begin(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse_emit(self, obj):
        line = "data: %s\n\n" % json.dumps(obj)
        self.wfile.write(line.encode("utf-8"))
        self.wfile.flush()

    def _path(self):
        return urllib.parse.urlparse(self.path).path

    def _query(self):
        return urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query)

    def do_GET(self):
        path = self._path()
        ctx = self.server.ctx
        if path == "/":
            try:
                with open(WEBUI_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html")
            except OSError:
                self._json({"error": "webui.html not found next to "
                                     "serve.py; run mule serve from a "
                                     "source checkout"}, 500)
        elif path == "/api/info":
            self._json({"version": ctx.args.version
                        if hasattr(ctx.args, "version") else "dev",
                        "model": ctx.args.model,
                        "root": os.path.abspath(ctx.args.root)})
        elif path == "/api/sessions":
            items = []
            for name in sessions.list_sessions():
                try:
                    msgs = sessions.load_session(name)
                except ValueError:
                    continue
                full = os.path.join(sessions.session_dir(), name + ".jsonl")
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    mtime = 0
                items.append({"id": name, "preview": _preview(msgs),
                              "mtime": mtime})
            items.sort(key=lambda x: x["mtime"], reverse=True)
            self._json({"sessions": items})
        elif path == "/api/session":
            sid = self._query().get("id", [None])[0]
            if not sid:
                self._json({"error": "missing id"}, 400)
                return
            try:
                msgs = sessions.load_session(sid)
            except ValueError:
                self._json({"error": "no such session"}, 404)
                return
            self._json({"id": sid,
                        "messages": _public_messages(msgs)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self._path()
        if path == "/api/chat":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self._json({"error": "bad json body"}, 400)
                return
            message = (body.get("message") or "").strip()
            if not message:
                self._json({"error": "empty message"}, 400)
                return
            self._handle_chat(message, body.get("session_id"))
        elif path == "/api/session/delete":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self._json({"error": "bad json body"}, 400)
                return
            sid = body.get("id")
            if not sid:
                self._json({"error": "missing id"}, 400)
                return
            try:
                sessions.delete_session(sid)
            except ValueError:
                self._json({"error": "no such session"}, 404)
                return
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def _handle_chat(self, message, session_id):
        ctx = self.server.ctx
        if not _chat_lock.acquire(blocking=False):
            self._json({"error": "a chat is already running, "
                                 "wait for it to finish"}, 409)
            return
        try:
            self._run_chat(ctx, message, session_id)
        finally:
            _chat_lock.release()

    def _run_chat(self, ctx, message, session_id):
        args = ctx.args
        if session_id:
            try:
                messages = sessions.load_session(session_id)
            except ValueError:
                session_id = None
                messages = None
        else:
            messages = None
        if not session_id:
            session_id = sessions.auto_name()

        self._sse_begin()
        emit = self._sse_emit
        totals = {"in": 0, "out": 0}

        def on_token(t):
            try:
                emit({"type": "token", "text": t})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def chat_fn(msgs, tool_schemas):
            # stream tokens straight into the sse response
            return ctx.client.chat_stream(msgs, tool_schemas,
                                         on_token=on_token)

        def on_result(step, calls, results):
            try:
                emit({"type": "tool", "step": step,
                      "calls": [{"name": c["function"]["name"],
                                 "args": c["function"].get("arguments", "")}
                                for c in calls],
                      "results": results})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def usage_cb(step, usage):
            if usage:
                totals["in"] += usage.get("prompt_tokens", 0)
                totals["out"] += usage.get("completion_tokens", 0)
            return False

        try:
            out = agent.run(
                message, chat_fn, ctx.tools,
                system_prompt=ctx.system, messages=messages,
                max_steps=args.max_steps, on_result=on_result,
                usage_cb=usage_cb,
                context_budget=getattr(args, "context_budget", 100000))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            try:
                emit({"type": "error", "message": "%s" % e})
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        try:
            sessions.save_session(session_id, out)
        except OSError:
            pass
        cost = cost_for(args.model, totals["in"], totals["out"])
        try:
            emit({"type": "done", "session_id": session_id,
                  "answer": agent.last_answer(out),
                  "tokens_in": totals["in"], "tokens_out": totals["out"],
                  "cost": fmt_cost(cost)})
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(ctx, port=0):
    # port 0 picks a free one; always localhost, never public
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.ctx = ctx
    server.daemon_threads = True
    return server


def cmd_serve(rest):
    # mule serve [--port N] [--no-browser] [--model M] [--root R] ...
    # shares every model/root flag with the normal run.
    from main import parse_args, resolve_system, __version__
    from client import ChatClient
    from tools import ToolSet
    from ui import red
    port = 8321
    no_browser = False
    rest = list(rest)
    cleaned = []
    i = 0
    while i < len(rest):
        if rest[i] == "--port" and i + 1 < len(rest):
            try:
                port = int(rest[i + 1])
            except ValueError:
                print("error: --port needs a number", file=sys.stderr)
                return 2
            i += 2
        elif rest[i] == "--no-browser":
            no_browser = True
            i += 1
        else:
            cleaned.append(rest[i])
            i += 1
    args = parse_args(cleaned)
    args.version = __version__
    if not args.api_key:
        print(red("set OPENAI_API_KEY or pass --api-key"),
              file=sys.stderr)
        return 2
    client = ChatClient(args.base_url, args.api_key, args.model,
                        timeout=args.timeout, retries=args.retries,
                        temperature=args.temperature,
                        max_tokens=args.max_tokens, seed=args.seed,
                        trace_file=args.trace)
    tools = ToolSet(os.path.abspath(args.root))
    from plugins import load_plugins
    plugin_tools, _ = load_plugins()
    for t in plugin_tools:
        if t["name"] not in tools.tools:
            tools.tools[t["name"]] = t
    from main import apply_tool_gates
    apply_tool_gates(args, tools)
    system = resolve_system(args)
    ctx = Ctx(args, client, tools, system)
    server = make_server(ctx, port)
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("mule web ui on %s" % url)
    print("local only, close this terminal or ctrl-c to stop")
    if not no_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
