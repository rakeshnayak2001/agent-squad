"""Gemini API backend against a local fake of generativelanguage.googleapis.com."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import agent_squad as squad
from agent_squad.workers import QuotaError, gemini_api

# key -> behaviour
KEYS = {"good1": "ok", "good2": "ok", "quota": "quota", "daily": "daily", "bad": "bad"}
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        key = self.headers.get("x-goog-api-key")
        if KEYS.get(key) == "bad":
            return self._send(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                              "message": "API key not valid. Please pass a valid API key."}})
        self._send(200, {"models": [
            {"name": "models/gemini-3-pro-preview", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]})

    def do_POST(self):
        key = self.headers.get("x-goog-api-key")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((key, self.path, body))
        mode = KEYS.get(key)
        if mode == "quota":
            return self._send(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                              "message": "Quota exceeded for metric: requests per minute"}})
        if mode == "daily":
            return self._send(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                              "message": "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel"}})
        if mode == "bad":
            return self._send(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                              "message": "API key not valid. Please pass a valid API key."}})
        if "nosuch" in self.path:
            return self._send(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "model not found"}})
        text = body["contents"][0]["parts"][0]["text"]
        self._send(200, {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True},
                                                              {"text": f"echo[{key}]: {text}"}]}}],
                         "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "thoughtsTokenCount": 2},
                         "modelVersion": "gemini-3-pro-preview"})


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1beta"
    srv.shutdown()


@pytest.fixture
def cfg(tmp_path, monkeypatch, server):
    monkeypatch.setattr(gemini_api, "BASE", server)
    monkeypatch.setenv("SQUAD_CONFIG", str(tmp_path / "none.json"))
    SEEN.clear()
    c = {"state_dir": str(tmp_path / "state"), "fallback": [],
         "workers": {"gemini": {"accounts_dir": str(tmp_path / "acc"),
                                "accounts": [{"name": "acc1"}, {"name": "acc2"}]}}}
    return c


def keys(cfg, k1, k2):
    w = squad.get_worker("gemini", cfg)
    if k1:
        w.set_key("acc1", k1)
    if k2:
        w.set_key("acc2", k2)
    return w


def test_api_is_default_backend_and_answers(cfg):
    keys(cfg, "good1", "good2")
    r = squad.ask("gemini", "hi", system="SYS", json_mode=True, temperature=0, config=cfg, log=None)
    assert r.text == "echo[good1]: hi" and r.account == "acc1"
    assert (r.tokens_in, r.tokens_out) == (5, 5)
    key, path, body = SEEN[-1]
    assert path.endswith("/models/gemini-3-pro-preview:generateContent")
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert body["generationConfig"] == {"temperature": 0, "responseMimeType": "application/json"}


def test_failover_on_429_and_daily_cooldown(cfg):
    keys(cfg, "daily", "good2")
    r = squad.ask("gemini", "x", config=cfg, log=None)
    assert r.account == "acc2"
    st = {a["name"]: a for a in squad.get_worker("gemini", cfg).status()["accounts"]}
    assert st["acc1"]["cooldown_until"] and "PerDay" in st["acc1"]["last_error"]


def test_both_keys_out_of_quota(cfg):
    keys(cfg, "quota", "daily")
    with pytest.raises(QuotaError):
        squad.get_worker("gemini", cfg).generate("x")


def test_missing_and_bad_keys_are_skipped(cfg):
    keys(cfg, None, "good2")
    assert squad.ask("gemini", "x", config=cfg, log=None).account == "acc2"
    w = keys(cfg, "bad", None)
    w.key_file({"name": "acc2"}).unlink()
    with pytest.raises(squad.WorkerError, match="set-key acc1"):
        squad.get_worker("gemini", cfg).generate("x")


def test_status_signed_in_means_key_saved(cfg):
    keys(cfg, "good1", None)
    st = squad.get_worker("gemini", cfg).status()
    assert st["backend"] == "api" and st["up"]
    assert [a["signed_in"] for a in st["accounts"]] == [True, False]


def test_models_and_404_hint(cfg):
    w = keys(cfg, "good1", None)
    assert w.models() == ["gemini-3-pro-preview"]
    with pytest.raises(squad.WorkerError, match="squad gemini models"):
        squad.ask("gemini:nosuch-model", "x", config=cfg, log=None)


def test_api_key_env(cfg, monkeypatch):
    cfg["workers"]["gemini"]["accounts"] = [{"name": "e", "api_key_env": "MY_GEM_KEY"}]
    monkeypatch.setenv("MY_GEM_KEY", "good1")
    assert squad.ask("gemini", "x", config=cfg, log=None).account == "e"
