"""The language model behind the assistant: a local Qwen3-4B served by Ollama (Apache-2.0, runs offline).

    ARTEX_LLM_URL    default http://127.0.0.1:11434
    ARTEX_LLM_MODEL  default qwen3:4b

Only Ollama's /api/chat with tool calling is used. Any other backend (llama.cpp's server, a different model) can stand
in by implementing `chat()`; the agent never depends on which.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b"
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class LLMError(RuntimeError):
    pass


class OllamaChat:
    def __init__(self, url: str | None = None, model: str | None = None, timeout_s: float = 120.0):
        self.url = (url or os.environ.get("ARTEX_LLM_URL", DEFAULT_URL)).rstrip("/")
        self.model = model or os.environ.get("ARTEX_LLM_MODEL", DEFAULT_MODEL)
        self.timeout_s = timeout_s

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise LLMError(f"the model server at {self.url} did not answer ({type(exc).__name__}: {exc})") from exc

    def status(self) -> dict:
        try:
            req = urllib.request.Request(self.url + "/api/tags")
            with urllib.request.urlopen(req, timeout=3) as r:
                names = [m.get("name") for m in json.loads(r.read()).get("models", [])]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return dict(ok=False, url=self.url, model=self.model, detail="no model server (is Ollama running?)")
        have = any(n == self.model or n.split(":")[0] == self.model.split(":")[0] and self.model in n for n in names)
        return dict(ok=have, url=self.url, model=self.model,
                    detail="ready" if have else f"server up, model {self.model} not pulled (ollama pull {self.model})")

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """One turn. Returns {"content": str, "tool_calls": [{"name": str, "arguments": dict}]}."""
        out = self._post("/api/chat", dict(model=self.model, messages=messages, tools=tools, stream=False, think=False,
                                           options=dict(temperature=0.2, num_ctx=8192)))
        msg = out.get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_unparsed": args}
            calls.append(dict(name=fn.get("name", ""), arguments=args if isinstance(args, dict) else {}))
        return dict(content=THINK_RE.sub("", msg.get("content") or "").strip(), tool_calls=calls)
