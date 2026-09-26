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
