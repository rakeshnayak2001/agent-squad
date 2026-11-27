"""A local model through Ollama's HTTP API. Free and private."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import Result, Worker, WorkerError

DEFAULTS = {"url": "http://localhost:11434", "model": "qwen3:8b", "temperature": 0.3, "timeout": 600}


class OllamaWorker(Worker):
    name = "local"

    @property
    def cfg(self) -> dict:
        return {**DEFAULTS, **self.settings}

    def generate(self, prompt, *, system="", model=None, json_mode=False, temperature=None, task="") -> Result:
        c = self.cfg
        model = model or c["model"]
        body = {"model": model, "prompt": prompt, "system": system, "stream": False,
                "options": {"temperature": c["temperature"] if temperature is None else temperature}}
        if json_mode:
            body["format"] = "json"
        req = urllib.request.Request(c["url"].rstrip("/") + "/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=float(c["timeout"])) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise WorkerError(f"local model unavailable ({e}). Is Ollama running? `ollama serve`") from e
        text = out.get("response", "")
        # reasoning models (qwen3, deepseek-r1) may wrap their thinking in <think>...</think>
        if "</think>" in text:
            text = text.split("</think>", 1)[1]
        return Result(text.strip(), self.name, model, tokens_in=out.get("prompt_eval_count", 0),
                      tokens_out=out.get("eval_count", 0), seconds=time.time() - t0)

    def status(self) -> dict:
        c = self.cfg
        try:
            with urllib.request.urlopen(c["url"].rstrip("/") + "/api/tags", timeout=3) as r:
                models = [m["name"] for m in json.loads(r.read()).get("models", [])]
            return {"name": self.name, "up": True, "url": c["url"], "models": models, "model": c["model"],
                    "model_ready": any(m == c["model"] or m.split(":")[0] == c["model"] for m in models)}
        except Exception as e:  # noqa: BLE001
            return {"name": self.name, "up": False, "url": c["url"], "models": [], "model": c["model"],
                    "model_ready": False, "error": str(e)}
