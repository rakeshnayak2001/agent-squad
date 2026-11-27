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
    monkeypatch.delenv("AGY_FAKE_MODE", raising=False)
    return {"state_dir": str(tmp_path / "state"), "fallback": [],
            "workers": {"gemini": {"backend": "agy", "accounts_dir": str(tmp_path / "acc")},
                        "local": {"url": "http://127.0.0.1:9", "timeout": 2}}}


def test_agy_answers_with_one_pro_account(cfg):
    r = squad.ask("gemini", "hello", system="SYS", config=cfg, log=None)
    assert r.text == "agy[default]: [Instructions]\nSYS\n\nhello" or r.text.startswith("agy[default]: [Instructions]")
    assert r.account == "pro" and (r.tokens_in, r.tokens_out) == (8, 7)
    st = squad.get_worker("gemini", cfg).status()
    assert st["backend"] == "agy" and [a["name"] for a in st["accounts"]] == ["pro"]
    assert st["accounts"][0]["signed_in"] is True


def test_agy_model_flag(cfg):
    cfg["workers"]["gemini"]["agy_model"] = "gemini-3.1-pro-high"
    assert squad.ask("gemini", "x", config=cfg, log=None).text.startswith("agy[gemini-3.1-pro-high]")


def test_long_prompt_goes_via_file(cfg):
    r = squad.ask("gemini", "Z" * 25000, config=cfg, log=None)
    assert "FILE:ZZZZ" in r.text


def test_quota_cools_down_and_falls_back(cfg, monkeypatch):
    monkeypatch.setenv("AGY_FAKE_MODE", "quota")
    with pytest.raises(QuotaError):
        squad.get_worker("gemini", cfg).generate("x")
    st = squad.get_worker("gemini", cfg).status()["accounts"][0]
    assert st["cooldown_until"]
    # while cooling down, a chain gemini -> claude-free fallback: next worker in chain is used
    with pytest.raises(WorkerError, match="no worker could answer"):
        squad.ask("gemini", "x", config=cfg, log=None, fallback=["local"])


def test_auth_marks_not_signed_in(cfg, monkeypatch):
    monkeypatch.setenv("AGY_FAKE_MODE", "auth")
    with pytest.raises(WorkerError, match="squad gemini login pro"):
        squad.get_worker("gemini", cfg).generate("x")
    assert squad.get_worker("gemini", cfg).status()["accounts"][0]["signed_in"] is False


def test_models(cfg):
    assert squad.get_worker("gemini", cfg).models() == ["gemini-3.1-pro-high", "gemini-3.7-flash"]


def test_missing_agy(cfg):
    cfg["workers"]["gemini"]["agy_command"] = "no-such-agy"
    st = squad.get_worker("gemini", cfg).status()
    assert not st["up"] and "install.ps1" in st["help"]
