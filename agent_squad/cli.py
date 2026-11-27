"""squad: command line for agent-squad (Claude master, pluggable AI workers).

    squad init                          write ~/.agent-squad/squad.json with defaults
    squad status                        which workers are ready, Gemini accounts
    squad test <spec> "prompt"          e.g. squad test gemini "Say hi in five words"
    squad ask <spec> [--json] < prompt.txt
    squad gemini set-key <account>      save an AI Studio API key for that slot (hidden input)
    squad gemini models [account]       models your key can use
    squad gemini login [account]        set-key (api backend) / sign-in (agy: Pro account, slot "pro")
    squad gemini use agy|api|cli        switch the Gemini backend in squad.json
    squad accounts [--reset [name]]     Gemini account cooldowns
    squad usage                         last 30 days by worker
    squad mcp                           run the MCP server (for Claude Code)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import ask, get_worker, load_config, state_dir, status, usage
from .config import config_path, write_default_config
from .workers import WorkerError


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser(prog="squad", description="multi-AI harness: Claude master, pluggable workers")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("init")
    sub.add_parser("status")
    t = sub.add_parser("test"); t.add_argument("spec"); t.add_argument("prompt", nargs="+")
    a = sub.add_parser("ask"); a.add_argument("spec"); a.add_argument("--json", action="store_true")
    a.add_argument("--system", default=""); a.add_argument("--no-fallback", action="store_true")
    g = sub.add_parser("gemini"); g.add_argument("action", choices=["login", "set-key", "models", "use"])
    g.add_argument("account", nargs="?")
    ac = sub.add_parser("accounts"); ac.add_argument("--reset", nargs="?", const="*", default=None)
    sub.add_parser("usage")
    sub.add_parser("mcp")
    args = p.parse_args(argv)
    cfg = load_config()

    if args.cmd == "init":
        print(f"Config: {write_default_config()}")
    elif args.cmd == "status":
        print(json.dumps({"config": str(config_path()), "workers": status(cfg)}, indent=2))
    elif args.cmd in ("test", "ask"):
        prompt = " ".join(args.prompt) if args.cmd == "test" else sys.stdin.read()
        try:
            r = ask(args.spec, prompt, task=args.cmd, config=cfg, json_mode=getattr(args, "json", False),
                    system=getattr(args, "system", ""), fallback=[] if args.cmd == "test" or args.no_fallback else None,
                    log=lambda m: print(m, file=sys.stderr))
        except WorkerError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            return 1
        print(r.text)
        print(json.dumps(r.to_dict() | {"text": None}), file=sys.stderr)
    elif args.cmd == "gemini":
        w = get_worker("gemini", cfg)
        if args.action == "models":
            try:
                print("\n".join(w.models(args.account)))
            except WorkerError as e:
                print(f"FAILED: {e}", file=sys.stderr)
                return 1
            return 0
        if args.action == "use":
            if args.account not in ("agy", "api", "cli"):
                p.error("usage: squad gemini use agy|api|cli")
            path = write_default_config()
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("workers", {}).setdefault("gemini", {})["backend"] = args.account
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print(f"Gemini backend set to {args.account} in {path}")
            return 0
        if not args.account:
            if w.backend == "agy":
                args.account = w.cfg["accounts"][0]["name"]
            else:
                p.error("gemini login/set-key needs an account name, e.g. acc1")
        return w.prompt_key(args.account) if args.action == "set-key" else w.login(args.account)
    elif args.cmd == "accounts":
        w = get_worker("gemini", cfg)
        if args.reset:
            w.pool.reset(None if args.reset == "*" else args.reset)
        print(json.dumps(w.status()["accounts"], indent=2))
    elif args.cmd == "usage":
        print(json.dumps(usage.summary(state_dir(cfg) / "usage.json")["total"], indent=2))
    elif args.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
