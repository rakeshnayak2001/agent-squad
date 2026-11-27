"""MCP server: gives an interactive Claude Code session (the master) worker tools.

    claude mcp add workers -- python -m agent_squad mcp        (or the .mcp.json in your project)

Tools: list_workers, worker_generate, worker_generate_to_file, worker_batch. Each takes
worker="local" | "gemini" | "<worker>:<model>". Long outputs go to files so they never fill Claude's context.

Set SQUAD_PROJECT_ROOT to the folder files may be read from / written to (default: current folder).
A host app can pass its own settings with SQUAD_SETTINGS_MODULE=<module> exposing squad_config().
"""
from __future__ import annotations

import glob
import importlib
import json
import os
from pathlib import Path

MAX_RETURN_CHARS = 4000


def _root() -> Path:
    return Path(os.environ.get("SQUAD_PROJECT_ROOT") or os.getcwd()).resolve()


def _cfg() -> tuple[dict | None, str | None]:
    mod = os.environ.get("SQUAD_SETTINGS_MODULE")
    if mod:
        m = importlib.import_module(mod)
        return m.squad_config(), getattr(m, "USAGE_FILE", None)
    return None, None


def _ask(worker: str, prompt: str, system: str = "", json_mode: bool = False) -> str:
    import agent_squad as harness
    cfg, ledger = _cfg()
    r = harness.ask(worker or None, prompt, task="mcp", system=system, json_mode=json_mode, config=cfg,
                    fallback=[], usage_file=ledger, log=None)
    return r.text


def _inside(root: Path, p: Path) -> bool:
    return p == root or root in p.parents


def build():
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server
    except ImportError:  # mcp 2.x renamed FastMCP to MCPServer
        from mcp.server.mcpserver import MCPServer as Server
    mcp = Server("workers")

    @mcp.tool()
    def list_workers() -> str:
        """Show which workers are ready (local Ollama, Gemini accounts, Claude) and their models."""
        import agent_squad as harness
        cfg, _ = _cfg()
        st = harness.status(cfg)
        slim = {k: {f: v.get(f) for f in ("up", "model", "models", "rotation", "error", "help") if v.get(f) is not None}
                for k, v in st.items()}
        if "gemini" in st:
            slim["gemini"]["accounts"] = [{f: a[f] for f in ("name", "signed_in", "cooldown_until", "calls_today")}
                                          for a in st["gemini"].get("accounts", [])]
        return json.dumps(slim)

    @mcp.tool()
    def worker_generate(prompt: str, worker: str = "local", system: str = "", json_mode: bool = False) -> str:
        """Give ONE small, well-specified task to a worker and return its answer.
        worker: "local" (free Ollama), "gemini" (Gemini on the Pro accounts), or "<worker>:<model>".
        Good for: brainstorming lists, rewording, classifying, extracting fields, formatting.
        Not for: facts you must be sure of, final scripts, decisions. Review what it returns."""
        out = _ask(worker, prompt, system, json_mode)
        return out if len(out) <= MAX_RETURN_CHARS else out[:MAX_RETURN_CHARS] + "\n...[truncated]"

    @mcp.tool()
    def worker_generate_to_file(prompt: str, output_path: str, worker: str = "local", system: str = "") -> str:
        """Have a worker write a long output straight to a file (path relative to the project).
        Returns only a status line, so the long text never enters Claude's context."""
        root = _root()
        p = (root / output_path).resolve()
        if not _inside(root, p) or p == root:
            return "Refused: output_path must be inside the project folder."
        out = _ask(worker, prompt, system)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8")
        return f"Wrote {len(out)} chars to {p.relative_to(root)}"

    @mcp.tool()
    def worker_batch(instruction: str, input_glob: str, output_dir: str, worker: str = "local",
                     output_ext: str = ".txt", system: str = "") -> str:
        """Apply the same instruction to every file matching input_glob (relative to the project) and save
        each result in output_dir/<name><output_ext>. Returns a compact summary only.
        Test the instruction on one item with worker_generate first."""
        root = _root()
        files = [f for f in sorted(glob.glob(str(root / input_glob), recursive=True))
                 if _inside(root, Path(f).resolve())]
        if not files:
            return f"No files matched {input_glob}"
        out_dir = (root / output_dir).resolve()
        if not _inside(root, out_dir):
            return "Refused: output_dir must be inside the project folder."
        out_dir.mkdir(parents=True, exist_ok=True)
        ok, failed = 0, []
        for path in files:
            try:
                content = Path(path).read_text(encoding="utf-8", errors="ignore")
                out = _ask(worker, f"{instruction}\n\n--- INPUT ---\n{content}", system)
                (out_dir / (Path(path).stem + output_ext)).write_text(out, encoding="utf-8")
                ok += 1
            except Exception as e:  # noqa: BLE001
                failed.append(f"{os.path.basename(path)}: {e}")
        msg = f"Done {ok}/{len(files)} files with {worker} -> {out_dir.relative_to(root)}"
        return msg + ("\nFailed:\n" + "\n".join(failed[:20]) if failed else "")

    return mcp


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
