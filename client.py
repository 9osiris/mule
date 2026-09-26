import json
import time
import urllib.request
import urllib.error

RETRYABLE = (429, 500, 502, 503, 504)  # rate limit or server blew up


class ChatClient:
    """talks to any openai-compatible /v1/chat/completions endpoint."""

    def __init__(self, base_url, api_key, model, timeout=120,
                 retries=3, backoff=1.0, temperature=None,
                 max_tokens=None, seed=None, trace_file=None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.seed = seed
        self.trace_file = trace_file
        # agentrouter's waf blocks plain script clients, so send the
        # documented client headers when pointed at it
        self.extra_headers = {}
        if "agentrouter.org" in base_url:
            self.extra_headers = {
                "Originator": "codex_cli_rs",
                "Version": "0.101.0",
                "User-Agent": ("codex_cli_rs/0.101.0 "
                               "(Windows NT 10.0; Win64; x64)"),
            }

    def _headers(self):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }
        headers.update(self.extra_headers)
        return headers

    def _trace(self, record):
        # raw request/response pairs, one json object per line.
        # the api key is never in the body, only in the header,
        # which is not recorded.
        if not self.trace_file:
            return
        try:
            with open(self.trace_file, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass

    def _body(self, messages, tools=None, stop=None):
        # request body, sampling params only included when set
        body = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if self.seed is not None:
            body["seed"] = self.seed
        if stop:
            body["stop"] = stop
        return body

    def _post(self, req):
        # one post with retries on rate limits and server errors
        for attempt in range(self.retries + 1):
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                last = attempt == self.retries
                if e.code not in RETRYABLE or last:
                    detail = e.read().decode(errors="replace")[:500]
                    raise RuntimeError("api error %d after %d attempt(s): %s"
                                       % (e.code, attempt + 1, detail))
                time.sleep(self.backoff * (2 ** attempt))

    def chat(self, messages, tools=None, stop=None):
        body = self._body(messages, tools, stop=stop)

        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        resp = self._post(req)
        try:
            payload = json.load(resp)
        finally:
            resp.close()

        msg = payload["choices"][0]["message"]
        out = {"role": "assistant"}
        self._trace({"request": body, "response": payload})
        usage = payload.get("usage") or {}
        if usage:
            out["usage"] = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            }
        if msg.get("content"):
            out["content"] = msg["content"]
        if msg.get("tool_calls"):
            out["tool_calls"] = [
                {
                    "id": tc["id"],
                    # keep the type tag: strict gateways reject the
                    # echoed assistant message without it
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"].get("arguments") or "{}",
                    },
                }
                for tc in msg["tool_calls"]
            ]
        return out

    def chat_stream(self, messages, tools=None, on_token=None, stop=None):
        # same as chat() but reads server-sent events, calling
        # on_token(text) for each content chunk as it arrives
        body = self._body(messages, tools, stop=stop)
        body["stream"] = True
        # ask the server to send token counts in the last chunk
        body["stream_options"] = {"include_usage": True}

        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST",
        )
        resp = self._post(req)

        content_parts = []
        tool_calls = {}
        usage = None
        try:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                # some servers send data: null keepalives
                if not isinstance(chunk, dict):
                    continue
                if "usage" in chunk:
                    usage = chunk["usage"]
                choices = chunk.get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                    if on_token:
                        on_token(text)
                # tool calls arrive in pieces, one index at a time
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    entry = tool_calls.setdefault(
                        idx, {"id": None, "name": None, "arguments": ""})
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["arguments"] += fn["arguments"]
        finally:
            resp.close()

        out = {"role": "assistant"}
        if usage:
            out["usage"] = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            }
        content = "".join(content_parts)
        if content:
            out["content"] = content
        if tool_calls:
            out["tool_calls"] = [
                {
                    "id": e["id"] or "call_%d" % i,
                    # keep the type tag: strict gateways reject the
                    # echoed assistant message without it
                    "type": "function",
                    "function": {
                        "name": e["name"] or "",
                        "arguments": e["arguments"] or "{}",
                    },
                }
                for i, e in sorted(tool_calls.items())
            ]
        self._trace({"request": body, "response": out})
        return out
