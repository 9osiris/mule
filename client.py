import json
import urllib.request
import urllib.error


class ChatClient:
    """talks to any openai-compatible /v1/chat/completions endpoint."""

    def __init__(self, base_url, api_key, model, timeout=120):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages, tools=None):
        body = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError("api error %d: %s" % (e.code, detail))

        msg = payload["choices"][0]["message"]
        out = {"role": "assistant"}
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
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"].get("arguments") or "{}",
                    },
                }
                for tc in msg["tool_calls"]
            ]
        return out

    def chat_stream(self, messages, tools=None, on_token=None):
        # same as chat() but reads server-sent events, calling
        # on_token(text) for each content chunk as it arrives
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # ask the server to send token counts in the last chunk
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError("api error %d: %s" % (e.code, detail))

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
                    "function": {
                        "name": e["name"] or "",
                        "arguments": e["arguments"] or "{}",
                    },
                }
                for i, e in sorted(tool_calls.items())
            ]
        return out
