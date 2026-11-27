import json
import os
from pathlib import Path

import pytest

import agent_squad as squad
from agent_squad.workers import QuotaError, WorkerError

FAKES = Path(__file__).parent / "fakes"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{FAKES}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SQUAD_CONFIG", str(tmp_path / "none.json"))
    return {"state_dir": str(tmp_path / "state"), "fallback": [],
            "workers": {"gemini": {"backend": "cli", "accounts_dir": str(tmp_path / "acc"),
                                   "accounts": [{"name": "acc1"}, {"name": "acc2"}]},
                        "local": {"url": "http://127.0.0.1:9", "timeout": 2}}}


def set_mode(cfg, acc, mode):
    d = Path(cfg["workers"]["gemini"]["accounts_dir"]) / acc
    d.mkdir(parents=True, exist_ok=True)
    (d / "mode").write_text(mode)


def test_gemini_ok_uses_first_account(cfg):
    r = squad.ask("gemini", "hello", config=cfg, log=None)
    assert r.worker == "gemini" and r.account == "acc1"
    assert r.text.startswith("echo[acc1]: hello")
    assert (r.tokens_in, r.tokens_out) == (11, 9)
    assert r.model == "gemini-3-pro-preview"


def test_failover_on_quota_then_cooldown(cfg):
    set_mode(cfg, "acc1", "quota")
    r = squad.ask("gemini", "hi", config=cfg, log=None)
    assert r.account == "acc2"
    st = {a["name"]: a for a in squad.get_worker("gemini", cfg).status()["accounts"]}
    assert st["acc1"]["cooldown_until"] and st["acc2"]["calls_today"] == 1
    # acc1 stays skipped while cooling down even after it recovers
    set_mode(cfg, "acc1", "ok")
    assert squad.ask("gemini", "again", config=cfg, log=None).account == "acc2"
    squad.get_worker("gemini", cfg).pool.reset()
    assert squad.ask("gemini", "after reset", config=cfg, log=None).account == "acc1"


def test_all_accounts_quota_raises_quota(cfg):
    set_mode(cfg, "acc1", "quota")
    set_mode(cfg, "acc2", "daily")
    with pytest.raises(QuotaError):
        squad.get_worker("gemini", cfg).generate("x")
    with pytest.raises(QuotaError):  # now both are cooling down
        squad.get_worker("gemini", cfg).generate("x")


def test_auth_error_marks_needs_login_and_moves_on(cfg):
    set_mode(cfg, "acc1", "auth")
    r = squad.ask("gemini", "hi", config=cfg, log=None)
    assert r.account == "acc2"
    st = {a["name"]: a for a in squad.get_worker("gemini", cfg).status()["accounts"]}
    assert st["acc1"]["needs_login"] and not st["acc1"]["cooldown_until"]


def test_round_robin(cfg):
    cfg["workers"]["gemini"]["rotation"] = "round_robin"
    used = [squad.ask("gemini", f"n{i}", config=cfg, log=None).account for i in range(4)]
    assert used == ["acc1", "acc2", "acc1", "acc2"]


def test_long_prompt_goes_through_stdin(cfg):
    r = squad.ask("gemini", "L" * 7000, config=cfg, log=None)
    assert "LLLL" in r.text


def test_system_and_json_folded_into_prompt(cfg):
    r = squad.ask("gemini", "PROMPT", system="SYS", json_mode=True, config=cfg, log=None)
    assert "[Instructions]" in r.text


def test_fallback_chain_and_usage_ledger(cfg, tmp_path):
    set_mode(cfg, "acc1", "crash")
    set_mode(cfg, "acc2", "crash")
    with pytest.raises(WorkerError):
        squad.ask("gemini", "x", config=cfg, log=None, fallback=["local"])
    set_mode(cfg, "acc2", "ok")
    ledger = tmp_path / "u.json"
    r = squad.ask("local", "x", config=cfg, log=None, fallback=["gemini"], usage_file=ledger)
    assert r.worker == "gemini"
    day = next(iter(json.loads(ledger.read_text()).values()))
    assert day["local"]["failed"] == 1 and day["gemini"]["accounts"] == {"acc2": 1}


def test_missing_cli_is_clear(cfg, monkeypatch):
    cfg["workers"]["gemini"]["command"] = "no-such-gemini"
    with pytest.raises(WorkerError, match="not found on PATH"):
        squad.get_worker("gemini", cfg).generate("x")


def test_unknown_worker(cfg):
    with pytest.raises(WorkerError, match="unknown worker"):
        squad.ask("codex", "x", config=cfg, log=None)


def test_status_lists_all_workers(cfg):
    st = squad.status(cfg)
    assert set(st) >= {"claude", "local", "gemini"}
    assert st["gemini"]["up"] and len(st["gemini"]["accounts"]) == 2
    assert st["local"]["up"] is False
