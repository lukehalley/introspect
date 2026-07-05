#!/usr/bin/env python3
"""
collect_threads.py — turn coding-agent session transcripts into one compact, redacted digest.

The deterministic "eyes" of the introspect skill. It reads the past conversation threads that
Claude Code, OpenAI Codex, and Cursor each store on disk (in three completely different
formats), filters to a time window, redacts secrets, and emits ONE common digest: per session
it lists which agent it was, the human's typed prompts, the tools used, where tools errored,
and lightweight "friction" signals (corrections, repeated tools).

It deliberately does NOT decide what to learn. Judgement — is this a durable preference? a
recurring habit worth a rule? — is left to the model reading the digest. The script's job is to
extract clean, honest raw material from every agent and normalise it, so the skill reasons about
your habits across ALL your threads, not one tool at a time.

Read sources:
  claude  ~/.claude/projects/<project>/<session>.jsonl
  codex   ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
  cursor  ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb  (cursorDiskKV)

Stdlib only. No external dependencies.

Usage:
    python3 collect_threads.py                       # today, every installed agent
    python3 collect_threads.py --agent codex --days 7
    python3 collect_threads.py --agent all --since 2026-07-01 --until 2026-07-05 --format json
    python3 collect_threads.py --project ~/dev/myproject
    python3 collect_threads.py --selftest            # built-in tests, no real data
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

CLAUDE_ROOT = os.path.expanduser("~/.claude/projects")
CODEX_ROOT = os.path.expanduser("~/.codex/sessions")
CURSOR_GLOBAL_DB = os.path.expanduser(
    "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb")
CURSOR_HOME = os.path.expanduser("~/.cursor")

# --- Redaction --------------------------------------------------------------
# Best-effort scrub of secrets so a digest (which may be saved to a report or read aloud) never
# leaks a key. Not a security boundary — a courtesy.
_REDACT_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|bearer)\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pat in _REDACT_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


# --- Friction signal cues ---------------------------------------------------
# Phrases in a human prompt that hint at a correction / repeated ask. Signals for the model to
# weigh, never conclusions. A single hit means little; the same cue across sessions is the point.
_CORRECTION_CUES = [
    "no,", "nope", "actually", "instead", "don't", "do not", "stop",
    "again", "still not", "still doesn't", "that's wrong", "that's not right",
    "that's not what", "why did you", "you were supposed to", "undo", "revert",
    "not what i", "i said", "as i said", "like i said", "i already told you",
    "not that", "you keep", "every time", "you always", "you never",
    "please just", "that broke", "you broke", "this is wrong", "read the",
]
_CUE_RE = re.compile("|".join(re.escape(c) for c in _CORRECTION_CUES), re.IGNORECASE)


# --- Shared helpers ---------------------------------------------------------
def parse_ts(raw):
    """Parse an ISO-8601 UTC timestamp like '2026-06-21T18:38:10.999Z'."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def in_window(dt, since_dt, until_dt):
    if dt is None:
        return False
    if since_dt is not None and dt < since_dt:
        return False
    if until_dt is not None and dt >= until_dt:
        return False
    return True


def mtime_before(path, since_dt):
    """True if the file was last modified before the window (safe to skip)."""
    if since_dt is None:
        return False
    try:
        mt = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except OSError:
        return True
    return mt < since_dt - timedelta(hours=1)  # buffer for clock skew


def extract_text(content) -> str:
    """Flatten a message.content (string or list of blocks) into plain text (Claude shape)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
                elif block.get("type") == "tool_result":
                    parts.append(extract_text(block.get("content")))
    return "\n".join(p for p in parts if p)


class Session:
    __slots__ = ("id", "agent", "project", "git_branch", "title", "start", "end",
                 "models", "prompts", "tool_calls", "tool_errors", "corrections")

    def __init__(self, sid, agent):
        self.id = sid
        self.agent = agent
        self.project = None
        self.git_branch = None
        self.title = None
        self.start = None
        self.end = None
        self.models = set()
        self.prompts = []
        self.tool_calls = Counter()
        self.tool_errors = []
        self.corrections = []

    def note_time(self, dt):
        if dt is None:
            return
        if self.start is None or dt < self.start:
            self.start = dt
        if self.end is None or dt > self.end:
            self.end = dt

    def add_prompt(self, text, max_prompts=40, max_chars=600):
        text = redact((text or "").strip())
        if not text:
            return
        if len(text) > max_chars:
            text = text[:max_chars] + " …[truncated]"
        if len(self.prompts) < max_prompts:
            self.prompts.append(text)
        if _CUE_RE.search(text):
            self.corrections.append(text[:200])

    def add_tool(self, name):
        self.tool_calls[name or "?"] += 1

    def add_error(self, summary):
        summary = re.sub(r"\s+", " ", redact(summary or "")).strip()[:200]
        if summary:
            self.tool_errors.append(summary)

    def nonempty(self):
        return bool(self.prompts or self.tool_calls)


# --- Adapter: Claude Code ---------------------------------------------------
def _is_human_prompt_claude(rec):
    if rec.get("type") != "user" or rec.get("isMeta") or "toolUseResult" in rec:
        return False
    if rec.get("promptSource") not in ("typed", "command"):
        return False
    return (rec.get("message") or {}).get("role") == "user"


def _tool_result_is_error_claude(rec):
    tur = rec.get("toolUseResult")
    if isinstance(tur, dict) and (tur.get("is_error") or tur.get("isError") or tur.get("error")):
        return True
    for block in ((rec.get("message") or {}).get("content") or []):
        if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
            return True
    return False


def collect_claude(since_dt, until_dt, project_filter=None, root=CLAUDE_ROOT, **kw):
    if not os.path.isdir(root):
        return []
    sessions = {}
    titles = {}
    for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
        if project_filter and project_filter.replace("/", "-") not in os.path.basename(os.path.dirname(path)):
            continue
        if mtime_before(path, since_dt):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            sid = rec.get("sessionId")
            if rec.get("type") == "ai-title" and sid and rec.get("aiTitle"):
                titles[sid] = rec["aiTitle"]
                continue
            dt = parse_ts(rec.get("timestamp"))
            if not in_window(dt, since_dt, until_dt) or not sid:
                continue
            s = sessions.get(sid) or sessions.setdefault(sid, Session(sid, "claude"))
            s.note_time(dt)
            if rec.get("cwd") and not s.project:
                s.project = rec["cwd"]
            if rec.get("gitBranch") and not s.git_branch:
                s.git_branch = rec["gitBranch"]
            rtype = rec.get("type")
            if rtype == "assistant":
                msg = rec.get("message") or {}
                if msg.get("model") and msg["model"] != "<synthetic>":
                    s.models.add(msg["model"])
                for block in (msg.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        s.add_tool(block.get("name"))
            elif rtype == "user":
                if _is_human_prompt_claude(rec):
                    s.add_prompt(extract_text((rec.get("message") or {}).get("content")), **kw)
                elif "toolUseResult" in rec and _tool_result_is_error_claude(rec):
                    s.add_error(extract_text((rec.get("message") or {}).get("content")))
    for sid, s in sessions.items():
        s.title = titles.get(sid) or (s.prompts[0][:80] if s.prompts else "(untitled)")
    return [s for s in sessions.values() if s.nonempty()]


# --- Adapter: OpenAI Codex --------------------------------------------------
_CODEX_EXIT_RE = re.compile(r"exit(?:ed)? with code ([1-9]\d*)", re.IGNORECASE)


def collect_codex(since_dt, until_dt, project_filter=None, root=CODEX_ROOT, **kw):
    if not os.path.isdir(root):
        return []
    sessions = []
    for path in sorted(glob.glob(os.path.join(root, "*", "*", "*", "*.jsonl"))):
        if mtime_before(path, since_dt):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        sid = os.path.basename(path).replace("rollout-", "").rsplit(".jsonl", 1)[0]
        s = Session(sid, "codex")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            payload = rec.get("payload") or {}
            rtype = rec.get("type")
            if rtype == "session_meta":
                s.id = payload.get("id") or sid
                if payload.get("cwd"):
                    s.project = payload["cwd"]
                continue
            if rtype == "turn_context":
                if payload.get("model"):
                    s.models.add(payload["model"])
                if payload.get("cwd") and not s.project:
                    s.project = payload["cwd"]
                continue
            dt = parse_ts(rec.get("timestamp"))
            if not in_window(dt, since_dt, until_dt):
                continue
            s.note_time(dt)
            ptype = payload.get("type")
            if rtype == "event_msg" and ptype == "user_message":
                s.add_prompt(payload.get("message", ""), **kw)
            elif rtype == "response_item" and ptype == "function_call":
                s.add_tool(payload.get("name"))
            elif rtype == "response_item" and ptype == "function_call_output":
                out = payload.get("output")
                if isinstance(out, dict):
                    out = out.get("content") or json.dumps(out)
                if isinstance(out, str) and _CODEX_EXIT_RE.search(out):
                    s.add_error(out)
        if project_filter and (not s.project or project_filter not in s.project):
            continue
        if s.nonempty() and s.start is not None:
            s.title = s.prompts[0][:80] if s.prompts else "(codex thread)"
            sessions.append(s)
    return sessions


# --- Adapter: Cursor --------------------------------------------------------
# Cursor keeps conversations in a SQLite kv store (cursorDiskKV): one composerData:<id> row per
# thread, plus one bubbleId:<id>:<bubble> row per message. type 1 = user, type 2 = assistant, and
# a bubble carrying toolFormerData is a tool call. Opened strictly read-only so we never lock or
# mutate the live DB. Guarded throughout because the schema drifts across Cursor versions and this
# adapter often runs on a machine where Cursor isn't even installed.
def _cursor_load_json(val):
    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def collect_cursor(since_dt, until_dt, project_filter=None, db_path=CURSOR_GLOBAL_DB,
                   max_scan=4000, **kw):
    if not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return []
    sessions = []
    try:
        rows = con.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%' "
            "ORDER BY ROWID DESC LIMIT ?", (max_scan,)).fetchall()
    except sqlite3.Error:
        con.close()
        return []
    for key, val in rows:
        d = _cursor_load_json(val)
        if not isinstance(d, dict):
            continue
        ts_ms = d.get("lastUpdatedAt") or d.get("createdAt")
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else None
        # If we have a timestamp, window-filter on it. If we don't and a window is set, we can't
        # confirm it belongs in-window, so skip rather than dump the whole (possibly huge) history.
        if dt is not None:
            if not in_window(dt, since_dt, until_dt):
                continue
        elif since_dt is not None:
            continue
        composer_id = d.get("composerId") or key.split(":", 1)[-1]
        s = Session(f"cursor:{composer_id}", "cursor")
        s.title = d.get("name") or "(cursor thread)"
        s.note_time(dt)
        bubbles = []
        headers = d.get("fullConversationHeadersOnly")
        if isinstance(headers, list):
            for h in headers:
                bid = (h or {}).get("bubbleId")
                if not bid:
                    continue
                try:
                    r = con.execute("SELECT value FROM cursorDiskKV WHERE key = ?",
                                    (f"bubbleId:{composer_id}:{bid}",)).fetchone()
                except sqlite3.Error:
                    r = None
                if r:
                    b = _cursor_load_json(r[0])
                    if isinstance(b, dict):
                        bubbles.append(b)
        elif isinstance(d.get("conversation"), list):
            bubbles = [b for b in d["conversation"] if isinstance(b, dict)]
        for b in bubbles:
            tfd = b.get("toolFormerData")
            if isinstance(tfd, dict):
                s.add_tool(tfd.get("name"))
                if tfd.get("userDecision") == "rejected":
                    s.add_error(f"user rejected tool: {tfd.get('name')}")
                continue
            if b.get("type") == 1 and (b.get("text") or "").strip():
                s.add_prompt(b.get("text", ""), **kw)
        if project_filter and (not s.project or project_filter not in s.project):
            continue
        if s.nonempty():
            sessions.append(s)
    con.close()
    return sessions


# --- Orchestration ----------------------------------------------------------
ADAPTERS = {"claude": collect_claude, "codex": collect_codex, "cursor": collect_cursor}


def detect_agents():
    present = []
    if os.path.isdir(CLAUDE_ROOT):
        present.append("claude")
    if os.path.isdir(CODEX_ROOT):
        present.append("codex")
    if os.path.exists(CURSOR_GLOBAL_DB) or os.path.isdir(CURSOR_HOME):
        present.append("cursor")
    return present or ["claude"]


def collect(agents, since_dt, until_dt, project_filter=None, **kw):
    out = []
    for agent in agents:
        fn = ADAPTERS.get(agent)
        if not fn:
            continue
        try:
            out.extend(fn(since_dt, until_dt, project_filter=project_filter, **kw))
        except Exception as exc:  # an adapter must never sink the whole run
            print(f"warning: {agent} adapter failed: {exc}", file=sys.stderr)
    return out


def build_digest(sessions, since_dt, until_dt, repeat_tool_threshold=8):
    ordered = sorted(sessions, key=lambda s: (s.start or datetime.max.replace(tzinfo=timezone.utc)))
    out_sessions = []
    correction_terms = Counter()
    projects_touched = Counter()
    by_agent = Counter()
    for s in ordered:
        by_agent[s.agent] += 1
        projects_touched[s.project or "?"] += 1
        for c in s.corrections:
            for cue in _CORRECTION_CUES:
                if cue in c.lower():
                    correction_terms[cue] += 1
        repeated = [f"{n} x{c}" for n, c in s.tool_calls.most_common() if c >= repeat_tool_threshold]
        out_sessions.append({
            "session_id": s.id,
            "agent": s.agent,
            "project": s.project,
            "git_branch": s.git_branch,
            "title": s.title,
            "started": s.start.isoformat() if s.start else None,
            "ended": s.end.isoformat() if s.end else None,
            "models": sorted(s.models),
            "num_prompts": len(s.prompts),
            "prompts": s.prompts,
            "tool_calls": dict(s.tool_calls.most_common()),
            "tool_errors": s.tool_errors[:10],
            "signals": {"correction_prompts": s.corrections[:10], "repeated_tools": repeated},
        })
    totals = {
        "sessions": len(out_sessions),
        "by_agent": dict(by_agent.most_common()),
        "projects": len(projects_touched),
        "user_prompts": sum(x["num_prompts"] for x in out_sessions),
        "tool_calls": sum(sum(x["tool_calls"].values()) for x in out_sessions),
        "tool_errors": sum(len(x["tool_errors"]) for x in out_sessions),
    }
    return {
        "window": {"since": since_dt.isoformat() if since_dt else None,
                   "until": until_dt.isoformat() if until_dt else None},
        "totals": totals,
        "cross_session": {
            "recurring_correction_cues": dict(correction_terms.most_common(15)),
            "projects_touched": dict(projects_touched.most_common()),
        },
        "sessions": out_sessions,
    }


def _oneline(text):
    return re.sub(r"[ ]*\n[ ]*", " ", text).strip()


def to_markdown(digest):
    w, t = digest["window"], digest["totals"]
    L = [f"# Thread digest ({w.get('since', '?')} → {w.get('until') or 'now'})", ""]
    agents = ", ".join(f"{k}:{v}" for k, v in t["by_agent"].items()) or "none"
    L.append(f"**{t['sessions']} sessions** ({agents}) across **{t['projects']} projects** · "
             f"{t['user_prompts']} prompts · {t['tool_calls']} tool calls · {t['tool_errors']} tool errors")
    L.append("")
    cues = digest["cross_session"]["recurring_correction_cues"]
    if cues:
        L.append("**Recurring correction cues (across sessions):** "
                 + ", ".join(f"`{k}`×{v}" for k, v in cues.items()))
        L.append("")
    for s in digest["sessions"]:
        L.append(f"## [{s['agent']}] {s['title']}")
        meta = s["project"] or "?"
        if s["git_branch"]:
            meta += f" · {s['git_branch']}"
        if s["models"]:
            meta += f" · {', '.join(s['models'])}"
        L.append(f"_{meta}_  ")
        L.append(f"session `{s['session_id']}`")
        L.append("")
        if s["prompts"]:
            L.append("**Prompts:**")
            L += [f"- {_oneline(p)}" for p in s["prompts"]]
            L.append("")
        if s["tool_calls"]:
            L.append("**Tools:** " + ", ".join(f"{k}×{v}" for k, v in s["tool_calls"].items()))
        if s["signals"]["repeated_tools"]:
            L.append("**Repeated tools:** " + ", ".join(s["signals"]["repeated_tools"]))
        if s["tool_errors"]:
            L.append("**Tool errors:**")
            L += [f"- {e}" for e in s["tool_errors"]]
        if s["signals"]["correction_prompts"]:
            L.append("**Correction signals:**")
            L += [f"- {_oneline(c)}" for c in s["signals"]["correction_prompts"]]
        L.append("")
    return "\n".join(L)


def resolve_window(args):
    if args.all:
        return None, None
    if args.since or args.until:
        since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None
        until_dt = ((datetime.fromisoformat(args.until) + timedelta(days=1)).replace(tzinfo=timezone.utc)
                    if args.until else None)
        return since_dt, until_dt
    if args.days is not None:
        start = (datetime.now().astimezone() - timedelta(days=args.days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(timezone.utc), None
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), None


def resolve_agents(arg):
    if arg in (None, "auto"):
        return detect_agents()
    if arg == "all":
        return ["claude", "codex", "cursor"]
    return [arg]


# --- Self-test --------------------------------------------------------------
def selftest():
    import tempfile
    now = datetime.now(timezone.utc)
    ts = now.isoformat().replace("+00:00", "Z")
    since = now - timedelta(days=1)

    with tempfile.TemporaryDirectory() as tmp:
        # ---- Claude ----
        cl_root = os.path.join(tmp, "claude")
        proj = os.path.join(cl_root, "-tmp-proj")
        os.makedirs(proj)
        claude_recs = [
            {"type": "ai-title", "sessionId": "s1", "aiTitle": "Fix billing"},
            {"type": "user", "sessionId": "s1", "timestamp": ts, "cwd": "/tmp/proj",
             "promptSource": "typed", "message": {"role": "user", "content": "refactor auth"}},
            {"type": "user", "sessionId": "s1", "timestamp": ts, "promptSource": "typed",
             "message": {"role": "user", "content": [{"type": "text",
                         "text": "no, don't touch tests, key sk-ant-abcdefabcdef1234567890"}]}},
            {"type": "assistant", "sessionId": "s1", "timestamp": ts,
             "message": {"role": "assistant", "model": "claude-opus-4-8",
                         "content": [{"type": "tool_use", "name": "Edit", "id": "t", "input": {}}]}},
            {"type": "user", "sessionId": "s1", "timestamp": ts, "toolUseResult": {"is_error": True},
             "message": {"role": "user", "content": [{"type": "tool_result", "is_error": True,
                         "content": "Error: nope"}]}},
            {"type": "user", "sessionId": "s1", "isMeta": True, "timestamp": ts,
             "promptSource": "typed", "message": {"role": "user", "content": "ignore me"}},
        ]
        with open(os.path.join(proj, "s1.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in claude_recs))
        cl = collect_claude(since, None, root=cl_root)
        assert len(cl) == 1, cl
        assert len(cl[0].prompts) == 2, cl[0].prompts
        assert "[REDACTED]" in " ".join(cl[0].prompts) and "sk-ant" not in " ".join(cl[0].prompts)
        assert "ignore me" not in " ".join(cl[0].prompts)
        assert cl[0].tool_calls.get("Edit") == 1 and len(cl[0].tool_errors) == 1
        assert cl[0].corrections and cl[0].title == "Fix billing"

        # ---- Codex ----
        cx_root = os.path.join(tmp, "codex")
        day = os.path.join(cx_root, "2026", "02", "19")
        os.makedirs(day)
        codex_recs = [
            {"timestamp": ts, "type": "session_meta",
             "payload": {"id": "cx1", "cwd": "/tmp/proj", "timestamp": ts}},
            {"timestamp": ts, "type": "turn_context", "payload": {"model": "gpt-5.3-codex"}},
            {"timestamp": ts, "type": "event_msg",
             "payload": {"type": "user_message", "message": "actually use pnpm not npm"}},
            {"timestamp": ts, "type": "response_item",
             "payload": {"type": "function_call", "name": "exec_command", "arguments": "{}"}},
            {"timestamp": ts, "type": "response_item",
             "payload": {"type": "function_call_output", "output": "Process exited with code 1\n"}},
        ]
        with open(os.path.join(day, "rollout-2026-02-19T10-24-02-cx1.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in codex_recs))
        cx = collect_codex(since, None, root=cx_root)
        assert len(cx) == 1, cx
        assert cx[0].agent == "codex" and cx[0].project == "/tmp/proj"
        assert cx[0].prompts == ["actually use pnpm not npm"], cx[0].prompts
        assert cx[0].corrections, "codex correction cue missed"
        assert cx[0].tool_calls.get("exec_command") == 1
        assert len(cx[0].tool_errors) == 1 and "gpt-5.3-codex" in cx[0].models

        # ---- Cursor (synthetic sqlite) ----
        cur_db = os.path.join(tmp, "state.vscdb")
        con = sqlite3.connect(cur_db)
        con.execute("CREATE TABLE cursorDiskKV (key TEXT, value TEXT)")
        comp = {"composerId": "cur1", "name": "Fix redirect",
                "createdAt": int(now.timestamp() * 1000),
                "lastUpdatedAt": int(now.timestamp() * 1000),
                "fullConversationHeadersOnly": [{"bubbleId": "b1", "type": 1},
                                                {"bubbleId": "b2", "type": 2}]}
        con.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("composerData:cur1", json.dumps(comp)))
        con.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("bubbleId:cur1:b1",
                    json.dumps({"type": 1, "text": "stop using default exports"})))
        con.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("bubbleId:cur1:b2",
                    json.dumps({"type": 2, "text": "ok",
                                "toolFormerData": {"name": "run_terminal_cmd", "userDecision": "rejected"}})))
        con.commit()
        con.close()
        cur = collect_cursor(since, None, db_path=cur_db)
        assert len(cur) == 1, cur
        assert cur[0].agent == "cursor" and cur[0].title == "Fix redirect"
        assert cur[0].prompts == ["stop using default exports"], cur[0].prompts
        assert cur[0].corrections, "cursor correction cue missed"
        assert cur[0].tool_calls.get("run_terminal_cmd") == 1

        # ---- Merge + digest ----
        digest = build_digest(cl + cx + cur, since, None)
        assert digest["totals"]["sessions"] == 3
        assert digest["totals"]["by_agent"] == {"claude": 1, "codex": 1, "cursor": 1}, digest["totals"]["by_agent"]
        md = to_markdown(digest)
        assert "[codex]" in md and "[cursor]" in md and "[claude]" in md
    print("selftest: OK — claude, codex, and cursor adapters all pass")


def main():
    ap = argparse.ArgumentParser(description="Digest coding-agent transcripts for the introspect skill.")
    ap.add_argument("--agent", choices=["claude", "codex", "cursor", "all", "auto"], default="auto",
                    help="which agent's threads to read (default: auto-detect installed)")
    ap.add_argument("--days", type=int, default=None, help="last N days including today")
    ap.add_argument("--since", help="start date YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", help="end date YYYY-MM-DD (inclusive)")
    ap.add_argument("--all", action="store_true", help="no time filter (everything)")
    ap.add_argument("--project", help="only sessions whose cwd contains this path")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--out", help="write to file instead of stdout")
    ap.add_argument("--selftest", action="store_true", help="run built-in tests and exit")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    since_dt, until_dt = resolve_window(args)
    agents = resolve_agents(args.agent)
    sessions = collect(agents, since_dt, until_dt, project_filter=args.project)
    digest = build_digest(sessions, since_dt, until_dt)
    text = json.dumps(digest, indent=2, ensure_ascii=False) if args.format == "json" else to_markdown(digest)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out} — {digest['totals']['sessions']} sessions "
              f"({digest['totals']['by_agent']})", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
