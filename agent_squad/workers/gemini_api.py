"""Direct Gemini API calls (Google AI Studio keys). Standard library only.

    POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent
    header x-goog-api-key: <key>
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .base import AuthError, QuotaError, Result, WorkerError

BASE = "https://generativelanguage.googleapis.com/v1beta"


def _request(url: str, key: str, body: dict | None, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(raw).get("error", {})
        except ValueError:
            err = {}
        msg = f"{e.code} {err.get('status', '')} {err.get('message', raw[:300])}".strip()
        if e.code == 429 or err.get("status") == "RESOURCE_EXHAUSTED":
            raise QuotaError(msg) from e
        if e.code in (401, 403) or "API_KEY" in raw or "API key" in raw:
            raise AuthError(msg) from e
        if e.code == 404:
            raise WorkerError(msg + "  (model not found: run `squad gemini models` to list the ones your key can use)") from e
        raise WorkerError(msg) from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise WorkerError(f"Gemini API unreachable: {e}") from e


def generate(key: str, prompt: str, *, model: str, system: str = "", json_mode: bool = False,
             temperature: float | None = None, timeout: float = 600) -> Result:
    body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    gen: dict = {}
    if temperature is not None:
        gen["temperature"] = temperature
    if json_mode:
        gen["responseMimeType"] = "application/json"
    if gen:
        body["generationConfig"] = gen
    model = model.removeprefix("models/")
    t0 = time.time()
    out = _request(f"{BASE}/models/{model}:generateContent", key, body, timeout)
    cands = out.get("candidates") or []
    if not cands:
        reason = (out.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise WorkerError(f"Gemini returned no answer ({reason})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    u = out.get("usageMetadata") or {}
    return Result(text.strip(), "gemini", out.get("modelVersion") or model,
                  tokens_in=int(u.get("promptTokenCount", 0) or 0),
                  tokens_out=int(u.get("candidatesTokenCount", 0) or 0) + int(u.get("thoughtsTokenCount", 0) or 0),
                  seconds=time.time() - t0, raw=out)


def list_models(key: str, timeout: float = 30) -> list[str]:
    out = _request(f"{BASE}/models?pageSize=1000", key, None, timeout)
    return sorted(m["name"].removeprefix("models/") for m in out.get("models", [])
                  if "generateContent" in m.get("supportedGenerationMethods", []))


DAILY_HINT = re.compile(r"per.?day|PerDay|daily", re.I)
