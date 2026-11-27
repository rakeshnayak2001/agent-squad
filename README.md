# agent-squad

Claude Code is the **master**; other AIs are **workers** behind one small interface.
Use it on its own (CLI, Python, or an MCP server for Claude Code), or plug it into another app
such as [ai-youtube-shorts-automation](https://github.com/rakeshnayak2001/ai-youtube-shorts-automation).

| worker   | what it is                                   | auth                               |
|----------|----------------------------------------------|------------------------------------|
| `claude` | Claude Code headless (`claude -p`), master   | your Claude subscription           |
| `local`  | a local model through Ollama                 | none (free, private)               |
| `gemini` | Gemini CLI headless, default `gemini-3-pro-preview` | Google sign-in per account (Google AI Pro), or an API key |
| `codex`  | planned: same interface (`codex exec`)       |                                    |

A route ("spec") is `worker` or `worker:model`: `local:qwen3:8b`, `gemini:gemini-3-pro-preview`, `claude:opus`.
If a worker fails, the `fallback` list in the config is tried in order.

## Install (Windows)
```
git clone https://github.com/rakeshnayak2001/agent-squad "C:\AI Vid\agent-squad"
"C:\AI Vid\agent-squad\setup.bat"
```
That installs the package (`pip install -e .[mcp]`), Gemini CLI (`npm install -g @google/gemini-cli`), writes
`~/.agent-squad/squad.json`, and opens a sign-in for each Gemini account.

## Use
```
squad status                          # which workers are ready, Gemini accounts
squad test gemini "Say hi in five words"
squad ask local --json < prompt.txt
squad gemini login acc1               # one-time Google sign-in for that slot
squad accounts [--reset]              # cooldowns after quota hits
squad usage                           # calls / tokens by worker and account
squad mcp                             # MCP server for Claude Code
```
(`python -m agent_squad ...` works too.)

```python
import agent_squad as squad
r = squad.ask("gemini", "Five festival ideas as a JSON list", json_mode=True, task="ideas")
print(r.text, r.worker, r.model, r.account, r.tokens_in, r.tokens_out)
```

### In Claude Code (master hands work to workers)
```
claude mcp add workers -- python -m agent_squad mcp
```
Tools: `list_workers`, `worker_generate`, `worker_generate_to_file`, `worker_batch`, each with
`worker="local" | "gemini" | "<worker>:<model>"`. Files are limited to `SQUAD_PROJECT_ROOT` (default: current folder).

## Gemini accounts and rotation
- Each account has its own Gemini CLI home (`~/.agent-squad/gemini/<name>`, passed as `GEMINI_CLI_HOME`), so two
  Google sign-ins live side by side. Sign in once per account with `squad gemini login <name>`; the CLI caches it.
- `rotation: "failover"` (default): use accounts in order; an account that hits a quota or rate limit cools down
  (30 min for per-minute limits, until about midnight US Pacific for daily ones) and the next is used.
  `"round_robin"` spreads calls across accounts.
- An account with `"api_key_env": "MY_GEMINI_KEY"` uses that pay-as-you-go API key instead of a sign-in.
- Every call runs in an empty temp folder, so the CLI's file tools cannot touch your projects.
- Not yet verified on a real account: the `GEMINI_CLI_HOME` isolation, the exact quota error wording, and the
  `gemini-3-pro-preview` id. Google says Gemini CLI was replaced by Antigravity CLI on 18 June 2026 for unpaid and
  Google One users; if your plan moves too, set `workers.gemini.command` to the new CLI.

## Config (`~/.agent-squad/squad.json`, or `$SQUAD_CONFIG`)
```json
{
  "state_dir": "~/.agent-squad/state",
  "default_worker": "local",
  "fallback": ["local", "claude"],
  "workers": {
    "claude": {"model": "", "allow_web_search": true},
    "local":  {"url": "http://localhost:11434", "model": "qwen3:8b", "temperature": 0.3},
    "gemini": {"command": "gemini", "model": "gemini-3-pro-preview", "rotation": "failover",
               "accounts": [{"name": "acc1", "label": "Pro account 1"}, {"name": "acc2", "label": "Pro account 2"}]}
  }
}
```
A host app can pass its own dict instead (`squad.ask(..., config=...)`), or point the MCP server at it with
`SQUAD_SETTINGS_MODULE=<module>` exposing `squad_config()`.

## Add a worker (e.g. Codex)
1. `agent_squad/workers/codex.py`: subclass `Worker`, implement `generate()` returning a `Result`, and `status()`.
   Raise `QuotaError` / `AuthError` / `WorkerError` so rotation and fallback work.
2. Add it to `WORKERS` in `agent_squad/workers/__init__.py`.
3. Route tasks to `codex` or `codex:<model>`.

## Tests
`python -m pytest -q` (uses a fake Gemini CLI in `tests/fakes/`, no accounts needed).
