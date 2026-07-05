#!/usr/bin/env python3
"""
setup.py — interactive setup for the introspect skill (scan → recommend → apply).

The pattern (borrowed from buildooor's skillbox-quickstart): this script does the deterministic
half — it SCANS the machine (which agents are installed, how many threads exist, whether the daily
hook is wired, whether a config already exists) and emits a RECOMMENDATION plus a `decisions` list.
The *agent* then presents the summary and asks the user only the UNRESOLVED decisions (via
AskUserQuestion), builds a config, and calls `apply`. Choices persist to ~/.introspect/config.json
so future runs and the daily hook read them instead of re-asking.

Commands:
    setup.py scan [--json]                 # assess the machine, print recommendation + decisions
    setup.py apply --config '<json>'       # validate + write ~/.introspect/config.json
    setup.py apply --non-interactive       # write the recommended defaults, no questions
    setup.py show                           # print the current config (if any)
    setup.py hook-snippet                   # print the settings.json hook to install (agent applies it)
    setup.py --selftest                     # built-in tests

Stdlib only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_threads as ct  # noqa: E402  (sibling module: reuses roots + detect_agents)

STATE_DIR = os.path.expanduser("~/.introspect")
CONFIG_PATH = os.path.join(STATE_DIR, "config.json")
HOOK_PATH = os.path.expanduser("~/.claude/hooks/introspect-daily.sh")

VALID_SAFETY = ("propose-approve", "auto-low-risk")
VALID_SCHEDULE_MODES = ("daily", "every-n", "none")


# --- scan -------------------------------------------------------------------
def _count_recent(paths, days=30):
    """Count files modified within the last N days (cheap proxy for 'active threads')."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for p in paths:
        try:
            if datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc) >= cutoff:
                n += 1
        except OSError:
            continue
    return n


def scan(claude_root=None, codex_root=None, cursor_db=None, cursor_home=None, config_path=CONFIG_PATH):
    claude_root = claude_root or ct.CLAUDE_ROOT
    codex_root = codex_root or ct.CODEX_ROOT
    cursor_db = cursor_db if cursor_db is not None else ct.CURSOR_GLOBAL_DB
    cursor_home = cursor_home if cursor_home is not None else ct.CURSOR_HOME

    claude_files = glob.glob(os.path.join(claude_root, "*", "*.jsonl")) if os.path.isdir(claude_root) else []
    codex_files = glob.glob(os.path.join(codex_root, "*", "*", "*", "*.jsonl")) if os.path.isdir(codex_root) else []
    cursor_present = os.path.exists(cursor_db) or os.path.isdir(cursor_home)

    agents = {
        "claude": {"installed": os.path.isdir(claude_root),
                   "threads_total": len(claude_files), "threads_30d": _count_recent(claude_files)},
        "codex": {"installed": os.path.isdir(codex_root),
                  "threads_total": len(codex_files), "threads_30d": _count_recent(codex_files)},
        "cursor": {"installed": cursor_present, "threads_total": None, "threads_30d": None},
    }
    installed = [a for a, v in agents.items() if v["installed"]] or ["claude"]
    hook_installed = os.path.exists(HOOK_PATH)
    existing = load_config(config_path)

    multi = any(a in installed for a in ("codex", "cursor")) and len(installed) > 1
    rec = {
        "agents": installed,
        "window_days": 1,
        "safety": "propose-approve",
        "schedule": {"mode": "daily", "hour": 18, "enabled": True},
        # if you use more than one agent, prefer writing universal prefs to a shared AGENTS.md
        "universal_pref_target": "agents-md" if multi else "per-agent",
    }

    decisions = [
        {"id": "agents", "question": "Which agents should introspect read?",
         "options": installed, "recommended": installed,
         "resolved": True, "why": f"detected installed: {', '.join(installed)}"},
        {"id": "window_days", "question": "Default look-back window when you run it?",
         "options": ["today (1)", "last 7 days (7)"], "recommended": "today (1)", "resolved": False},
        {"id": "safety", "question": "How should changes be applied?",
         "options": ["propose-and-approve (recommended)", "auto-apply low-risk (memory only)"],
         "recommended": "propose-and-approve", "resolved": False},
        {"id": "schedule", "question": "Run automatically?",
         "options": ["daily evening hook (local, report-only)", "every ~10 sessions", "none / manual"],
         "recommended": "daily evening hook", "resolved": False},
    ]
    if multi:
        decisions.append({
            "id": "universal_pref_target",
            "question": "Where should preferences that apply to ALL your agents go?",
            "options": ["one shared AGENTS.md (recommended)", "duplicate per-agent"],
            "recommended": "one shared AGENTS.md", "resolved": False})

    return {
        "agents": agents,
        "installed": installed,
        "hook_installed": hook_installed,
        "config_exists": existing is not None,
        "current_config": existing,
        "recommendation": rec,
        "decisions": decisions,
    }


def scan_to_text(s):
    L = ["introspect setup — machine scan", ""]
    for a, v in s["agents"].items():
        if not v["installed"]:
            L.append(f"  {a:7} not installed")
        elif v["threads_total"] is None:
            L.append(f"  {a:7} installed")
        else:
            L.append(f"  {a:7} installed · {v['threads_total']} threads ({v['threads_30d']} in last 30d)")
    L.append("")
    L.append(f"  daily hook installed: {'yes' if s['hook_installed'] else 'no'}")
    L.append(f"  existing config:      {'yes' if s['config_exists'] else 'no'}")
    L.append("")
    L.append("Recommended defaults: " + json.dumps(s["recommendation"]))
    L.append("")
    L.append("Ask the user these (skip any marked resolved):")
    for d in s["decisions"]:
        mark = "✓ resolved" if d["resolved"] else "? ask"
        L.append(f"  [{mark}] {d['id']}: {d['question']}  (rec: {d['recommended']})")
    return "\n".join(L)


# --- config -----------------------------------------------------------------
def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def normalise_config(cfg):
    """Coerce a partial/loose config into a valid, complete one (defensive)."""
    out = {"version": 1}
    agents = cfg.get("agents") or ["claude"]
    out["agents"] = [a for a in agents if a in ("claude", "codex", "cursor")] or ["claude"]
    try:
        out["window_days"] = max(1, int(cfg.get("window_days", 1)))
    except (TypeError, ValueError):
        out["window_days"] = 1
    out["safety"] = cfg.get("safety") if cfg.get("safety") in VALID_SAFETY else "propose-approve"
    sched = cfg.get("schedule") or {}
    mode = sched.get("mode") if sched.get("mode") in VALID_SCHEDULE_MODES else "daily"
    try:
        hour = int(sched.get("hour", 18))
    except (TypeError, ValueError):
        hour = 18
    hour = min(23, max(0, hour))
    out["schedule"] = {"mode": mode, "hour": hour, "enabled": bool(sched.get("enabled", mode != "none"))}
    out["universal_pref_target"] = (cfg.get("universal_pref_target")
                                    if cfg.get("universal_pref_target") in ("agents-md", "per-agent")
                                    else "per-agent")
    return out


def apply_config(cfg, path=CONFIG_PATH):
    cfg = normalise_config(cfg)
    os.makedirs(os.path.join(os.path.dirname(path), "reports"), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return cfg


HOOK_STOP_SNIPPET = {
    "hooks": [{"type": "command", "command": f"bash {HOOK_PATH}"}]
}
HOOK_SESSIONSTART_SNIPPET = {
    "hooks": [{"type": "command",
               "command": "[ -f \"$HOME/.introspect/.pending\" ] && echo \"introspect: a fresh daily "
                          "thread digest is ready in ~/.introspect/reports/. Say 'run introspect' to "
                          "turn it into a reviewed changeset.\" || true"}]
}


def hook_snippet_text():
    return (
        "To wire the daily local run, add these to ~/.claude/settings.json (agent applies with "
        "approval; the hook script lives at " + HOOK_PATH + "):\n\n"
        "  hooks.Stop  += " + json.dumps(HOOK_STOP_SNIPPET) + "\n"
        "  hooks.SessionStart += " + json.dumps(HOOK_SESSIONSTART_SNIPPET) + "\n\n"
        "The hook is report-only: it writes ~/.introspect/reports/digest-<date>.md once each "
        "evening and never edits any config."
    )


# --- selftest ---------------------------------------------------------------
def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        # a machine with claude + codex, no cursor
        cl = os.path.join(tmp, "claude"); os.makedirs(os.path.join(cl, "-p"))
        open(os.path.join(cl, "-p", "s.jsonl"), "w").close()
        cx = os.path.join(tmp, "codex", "2026", "07", "05"); os.makedirs(cx)
        open(os.path.join(cx, "rollout-x.jsonl"), "w").close()
        cfg_path = os.path.join(tmp, "config.json")

        s = scan(claude_root=cl, codex_root=cx.split("/2026")[0], cursor_db=os.path.join(tmp, "none"),
                 cursor_home=os.path.join(tmp, "nohome"), config_path=cfg_path)
        assert s["agents"]["claude"]["installed"] and s["agents"]["claude"]["threads_total"] == 1
        assert s["agents"]["codex"]["installed"] and s["agents"]["codex"]["threads_total"] == 1
        assert not s["agents"]["cursor"]["installed"]
        assert set(s["installed"]) == {"claude", "codex"}
        assert s["recommendation"]["universal_pref_target"] == "agents-md", "multi-agent → shared AGENTS.md"
        assert any(d["id"] == "universal_pref_target" for d in s["decisions"])
        assert not s["config_exists"]
        assert scan_to_text(s)  # renders without error

        # apply + reload round-trips and normalises junk
        applied = apply_config({"agents": ["claude", "bogus"], "window_days": "7",
                                "schedule": {"mode": "weird", "hour": 99}}, path=cfg_path)
        assert applied["agents"] == ["claude"], applied
        assert applied["window_days"] == 7
        assert applied["schedule"]["mode"] == "daily" and applied["schedule"]["hour"] == 23
        assert applied["safety"] == "propose-approve"
        assert load_config(cfg_path)["window_days"] == 7

        s2 = scan(claude_root=cl, codex_root=cx.split("/2026")[0], cursor_db=os.path.join(tmp, "none"),
                  cursor_home=os.path.join(tmp, "nohome"), config_path=cfg_path)
        assert s2["config_exists"] and s2["current_config"]["window_days"] == 7
        assert "settings.json" in hook_snippet_text()
    print("selftest: OK — scan, recommend, apply, normalise all pass")


def main():
    ap = argparse.ArgumentParser(description="Interactive setup for the introspect skill.")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("scan", help="assess the machine and print recommendation + decisions")
    sp.add_argument("--json", action="store_true")

    ap_apply = sub.add_parser("apply", help="write ~/.introspect/config.json")
    ap_apply.add_argument("--config", help="JSON config object built from the user's answers")
    ap_apply.add_argument("--non-interactive", action="store_true", help="write recommended defaults")

    sub.add_parser("show", help="print the current config")
    sub.add_parser("hook-snippet", help="print the settings.json hook to install")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if args.cmd == "scan":
        s = scan()
        print(json.dumps(s, indent=2) if args.json else scan_to_text(s))
    elif args.cmd == "apply":
        if args.non_interactive:
            cfg = scan()["recommendation"]
        elif args.config:
            cfg = json.loads(args.config)
        else:
            print("apply needs --config '<json>' or --non-interactive", file=sys.stderr)
            sys.exit(2)
        written = apply_config(cfg)
        print("wrote " + CONFIG_PATH)
        print(json.dumps(written, indent=2))
    elif args.cmd == "show":
        cfg = load_config()
        print(json.dumps(cfg, indent=2) if cfg else "(no config yet — run: setup.py apply --non-interactive)")
    elif args.cmd == "hook-snippet":
        print(hook_snippet_text())
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
